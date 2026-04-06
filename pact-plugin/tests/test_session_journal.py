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
11. Returns False when session_dir is empty
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
23a. Skips truncated/incomplete JSON line at end of file
23b. Returns empty list when filtering by nonexistent event_type

read_last_event():
23. Returns the most recent event of given type
24. Returns None when no matching events
25. Returns None when journal is missing

get_journal_path():
26. Returns correct absolute path string
27. Does not check file existence
27a. Returns empty string when session_dir unavailable

read_events_from() — Explicit API:
50. Reads events from explicit session directory path
51. Filters by event_type
52. Returns empty list for missing journal
53. Returns empty list for empty session_dir
54. Skips malformed lines
55. Skips empty lines
56. Returns empty for nonexistent event_type filter

read_last_event_from() — Explicit API:
57. Returns most recent matching event
58. Returns None when no match
59. Returns None for missing journal
60. Returns None for empty session_dir
61. Skips malformed lines

_get_session_dir():
62. Returns session dir from mocked pact_context
63. Returns empty string when unavailable

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
41. restore_last_session uses journal when prev_session_dir provided
42. check_paused_state uses journal when prev_session_dir provided
43. _extract_prev_session_dir returns None on IOError (fail-open)

Integration: pact_context.write_context() cache fix:
64. Full path chain: write_context -> append_event -> file at sessions/ path
65. write_context populates _cache for immediate get_session_dir() access

CLI (main()):
44. write subcommand creates event and exits 0
45. read subcommand outputs JSON array and exits 0
46. read-last subcommand outputs single event JSON and exits 0
47. write with invalid --data JSON exits 1
48. write with missing --type exits non-zero
49. read-last with no matching events outputs "null"
"""
import json
import os
import subprocess
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
def session_dir(journal_home):
    """Standard test session directory path (string, not created on disk)."""
    return str(journal_home / ".claude" / "pact-sessions" / "test-project" / "test-session-id")


@pytest.fixture
def team_name():
    """Standard test team name (still used for non-journal tests)."""
    return "pact-test1234"


@pytest.fixture
def journal_file(session_dir):
    """Return the expected journal file path (does not create it)."""
    return Path(session_dir) / "session-journal.jsonl"


@pytest.fixture(autouse=True)
def mock_get_session_dir(monkeypatch, session_dir):
    """Mock _get_session_dir at the session_journal module level.

    Patches the internal _get_session_dir() function which is called by
    _journal_path() to derive the implicit path. This avoids import-path
    complications (shared.pact_context vs pact_context).
    """
    import shared.session_journal as sj
    monkeypatch.setattr(sj, "_get_session_dir", lambda: session_dir)


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

    def test_roundtrip_append_and_read(self, journal_home):
        """P0: Write event, read it back, all fields match."""
        from shared.session_journal import append_event, make_event, read_events

        event = make_event(
            "agent_handoff",
            agent="backend-coder",
            task_id="7",
            task_subject="CODE: implement auth",
            handoff={"produced": ["src/auth.ts"]},
        )

        result = append_event(event)
        assert result is True

        events = read_events()
        assert len(events) == 1

        read_back = events[0]
        assert read_back["v"] == 1
        assert read_back["type"] == "agent_handoff"
        assert read_back["agent"] == "backend-coder"
        assert read_back["task_id"] == "7"
        assert read_back["handoff"]["produced"] == ["src/auth.ts"]
        assert "ts" in read_back

    def test_creates_directory(self, journal_home, team_name, journal_file):
        """P0: mkdir -p behavior when sessions dir doesn't exist."""
        from shared.session_journal import append_event, make_event

        assert not journal_file.parent.exists()

        event = make_event(
            "session_start",
            team="test",
            session_id="test-session",
            project_dir="/tmp/test-project",
        )
        result = append_event(event)

        assert result is True
        assert journal_file.exists()

    def test_rejects_missing_v(self, journal_home):
        """P0: Returns False when v field is missing."""
        from shared.session_journal import append_event

        event = {"type": "test", "ts": "2026-01-01T00:00:00Z"}
        result = append_event(event)
        assert result is False

    def test_rejects_non_int_v(self, journal_home):
        """P0: Returns False when v is not an int."""
        from shared.session_journal import append_event

        event = {"v": "1", "type": "test", "ts": "2026-01-01T00:00:00Z"}
        result = append_event(event)
        assert result is False

    def test_rejects_bool_v_true(self, journal_home):
        """P0: Returns False when v is True (bool is subclass of int)."""
        from shared.session_journal import append_event

        event = {"v": True, "type": "test", "ts": "2026-01-01T00:00:00Z"}
        result = append_event(event)
        assert result is False

    def test_rejects_bool_v_false(self, journal_home):
        """P0: Returns False when v is False (bool is subclass of int)."""
        from shared.session_journal import append_event

        event = {"v": False, "type": "test", "ts": "2026-01-01T00:00:00Z"}
        result = append_event(event)
        assert result is False

    def test_rejects_missing_type(self, journal_home):
        """P0: Returns False when type field is missing."""
        from shared.session_journal import append_event

        event = {"v": 1, "ts": "2026-01-01T00:00:00Z"}
        result = append_event(event)
        assert result is False

    def test_rejects_empty_type(self, journal_home):
        """P0: Returns False when type is empty string."""
        from shared.session_journal import append_event

        event = {"v": 1, "type": "", "ts": "2026-01-01T00:00:00Z"}
        result = append_event(event)
        assert result is False

    def test_rejects_empty_session_dir(self, journal_home, monkeypatch):
        """P0: Returns False when session dir is empty (not initialized)."""
        from shared.session_journal import append_event, make_event
        import shared.session_journal as sj

        monkeypatch.setattr(sj, "_get_session_dir", lambda: "")
        event = make_event("test")
        result = append_event(event)
        assert result is False

    def test_fail_open_on_write_error(self, session_dir):
        """P0: Returns False on write error, no exception raised."""
        from shared.session_journal import append_event, make_event

        event = make_event("test")

        # Force a write error by making the parent read-only
        journal_dir = Path(session_dir)
        journal_dir.mkdir(parents=True)
        os.chmod(str(journal_dir), 0o444)

        try:
            result = append_event(event)
            # Should return False, not raise
            assert result is False
        finally:
            # Restore permissions for cleanup
            os.chmod(str(journal_dir), 0o755)

    def test_auto_sets_ts_when_missing(self, journal_home, team_name):
        """P0: Auto-sets timestamp when ts is not in the event dict."""
        from shared.session_journal import append_event, read_events

        event = {"v": 1, "type": "test_auto_ts"}
        result = append_event(event)
        assert result is True

        events = read_events()
        assert len(events) == 1
        assert "ts" in events[0]
        assert events[0]["ts"].endswith("Z")

    def test_file_permissions(self, journal_home, team_name, journal_file):
        """File should be created with 0o600 permissions."""
        from shared.session_journal import append_event, make_event

        event = make_event("test")
        append_event(event)

        assert journal_file.exists()
        stat = journal_file.stat()
        # Check file permission bits (masking away file type bits)
        assert stat.st_mode & 0o777 == 0o600

    def test_directory_permissions(self, journal_home, team_name, journal_file):
        """Parent directory should be created with 0o700 permissions."""
        from shared.session_journal import append_event, make_event

        event = make_event("test")
        append_event(event)

        dir_stat = journal_file.parent.stat()
        assert dir_stat.st_mode & 0o777 == 0o700

    def test_multiple_appends_sequential(self, journal_home):
        """Multiple sequential appends produce separate lines."""
        from shared.session_journal import append_event, make_event, read_events

        for i in range(5):
            event = make_event("test", seq=i)
            append_event(event)

        events = read_events()
        assert len(events) == 5
        for i, event in enumerate(events):
            assert event["seq"] == i

    def test_atomic_write_returns_false_on_oserror(self, tmp_path):
        """F4: _atomic_write fails open (returns False) when os.open raises OSError.

        Direct unit test of the underlying primitive used by append_event(). The
        function is currently exercised indirectly via append_event's fail-open
        path (test_fail_open_on_write_error uses chmod 0o444 on the parent dir),
        but a direct test pins the contract: any OSError from os.open must be
        swallowed and surface as a False return -- never propagate.

        Patch path: the string form `"shared.session_journal.os.open"` is the
        canonical pytest pattern for intercepting a stdlib function at the
        module where it is LOOKED UP. The earlier attribute-style patch
        (`patch.object(sj.os, "open", ...)`) worked only because the module
        does `import os` and looks up `os.open` at call time; if the code
        were ever refactored to `from os import open`, that form would
        silently no-op while the call still routed through the original
        stdlib function. The string form matches the rest of the codebase
        (see tests/test_memory_database.py patching `scripts.database.os.open`).
        """
        import shared.session_journal as sj

        target = tmp_path / "journal.jsonl"

        def _raise_oserror(*args, **kwargs):
            raise OSError("simulated disk failure")

        with patch("shared.session_journal.os.open", side_effect=_raise_oserror):
            result = sj._atomic_write(target, b"test payload")

        assert result is False
        # File should not exist (os.open raised before any bytes were written)
        assert not target.exists()

    def test_atomic_write_loops_over_short_writes(self, tmp_path):
        """BugF3: _atomic_write retries when os.write returns a short count.

        `os.write` may return fewer bytes than requested — signal interruption
        is the usual culprit; the O_APPEND atomicity guarantee is also only
        pinned up to PIPE_BUF on Linux, so a giant entry could be split by
        the kernel. Before this fix the function called `os.write(fd, data)`
        once and discarded the return value, silently losing any bytes
        beyond the short count. The loop now drains the buffer via a
        memoryview, retrying from where it left off until every byte has
        been written.

        This test fakes `os.write` to return a small positive count on each
        call, then falls back to the real write for the final tail so the
        file actually lands on disk and we can cross-check the contents.
        """
        import shared.session_journal as sj

        target = tmp_path / "journal.jsonl"
        payload = b"x" * 1000 + b"\n"  # 1001 bytes — well over any single chunk.

        real_os_write = sj.os.write
        chunks_written = []

        def short_write(fd, buf):
            # Every call writes at most 100 bytes so the loop must run at
            # least ceil(1001/100) = 11 times to drain the payload.
            mv = memoryview(buf)
            limit = 100
            if len(mv) > limit:
                mv = mv[:limit]
            n = real_os_write(fd, bytes(mv))
            chunks_written.append(n)
            return n

        with patch("shared.session_journal.os.write", side_effect=short_write):
            result = sj._atomic_write(target, payload)

        assert result is True, "short writes should loop to completion"
        assert sum(chunks_written) == len(payload), (
            f"sum of short writes ({sum(chunks_written)}) must equal the "
            f"payload size ({len(payload)}) — otherwise bytes were dropped"
        )
        # The file must contain the exact payload — no gaps, no duplication.
        assert target.read_bytes() == payload
        # And the loop must have taken more than one iteration; otherwise
        # the short-write scenario was not actually exercised.
        assert len(chunks_written) > 1, (
            f"expected multiple short writes but observed only "
            f"{len(chunks_written)} call(s) — patch may be ineffective"
        )

    def test_atomic_write_bails_out_on_non_progressing_write(self, tmp_path):
        """BugF3 edge case: a zero-return from os.write must not spin.

        If a fake `os.write` returns 0 (no progress, no exception), the loop
        must return False rather than spin forever. Real filesystems do not
        normally return 0 from a blocking write, but guarding the loop is
        cheap insurance against FUSE filesystems, test doubles, or a future
        refactor that accidentally configures a non-blocking fd.
        """
        import shared.session_journal as sj

        target = tmp_path / "journal.jsonl"

        call_counter = {"n": 0}

        def zero_write(fd, buf):
            call_counter["n"] += 1
            # Guard against the test itself hanging: after 10 zero returns
            # something is very wrong — raise to fail loudly.
            if call_counter["n"] > 10:
                raise AssertionError(
                    "loop spun past 10 iterations on zero-return write"
                )
            return 0

        with patch("shared.session_journal.os.write", side_effect=zero_write):
            result = sj._atomic_write(target, b"data")

        assert result is False
        # Exactly one iteration should have fired before bail-out; more than
        # that means the loop does not treat zero as terminal.
        assert call_counter["n"] == 1


# ---------------------------------------------------------------------------
# read_events()
# ---------------------------------------------------------------------------


class TestReadEvents:
    """Tests for read_events() -- JSONL reading with type filtering."""

    def test_returns_all_events_without_filter(self, journal_home, team_name):
        """P0: Returns all events when no type filter."""
        from shared.session_journal import append_event, make_event, read_events

        append_event(
            make_event(
                "session_start",
                team="t1",
                session_id="sid1",
                project_dir="/tmp/p1",
            ),
        )
        append_event(
            make_event(
                "agent_handoff",
                agent="coder",
                task_id="1",
                task_subject="s",
                handoff={},
            ),
        )
        append_event(make_event("session_end"))

        events = read_events()
        assert len(events) == 3
        assert events[0]["type"] == "session_start"
        assert events[1]["type"] == "agent_handoff"
        assert events[2]["type"] == "session_end"

    def test_filters_by_type(self, journal_home, team_name):
        """P0: Returns only matching events when type filter applied."""
        from shared.session_journal import append_event, make_event, read_events

        append_event(
            make_event(
                "session_start",
                team="t1",
                session_id="sid1",
                project_dir="/tmp/p1",
            ),
        )
        append_event(
            make_event(
                "agent_handoff",
                agent="coder1",
                task_id="1",
                task_subject="s",
                handoff={},
            ),
        )
        append_event(
            make_event("phase_transition", phase="CODE", status="started"),
        )
        append_event(
            make_event(
                "agent_handoff",
                agent="coder2",
                task_id="2",
                task_subject="s",
                handoff={},
            ),
        )

        handoffs = read_events(event_type="agent_handoff")
        assert len(handoffs) == 2
        assert handoffs[0]["agent"] == "coder1"
        assert handoffs[1]["agent"] == "coder2"

    def test_returns_empty_for_missing_file(self, journal_home, team_name):
        """P0: Returns empty list when journal file doesn't exist."""
        from shared.session_journal import read_events

        events = read_events()
        assert events == []

    def test_skips_malformed_lines(self, journal_home, team_name, journal_file):
        """P0: Malformed JSON lines are silently skipped."""
        from shared.session_journal import append_event, make_event, read_events

        # Write a valid event first
        append_event(make_event("test", seq=1))

        # Inject a malformed line directly
        with open(str(journal_file), "a") as f:
            f.write("this is not json\n")
            f.write('{"v":1,"type":"test","seq":2,"ts":"2026-01-01T00:00:00Z"}\n')

        events = read_events()
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

        events = read_events()
        assert len(events) == 2

    def test_fail_open_on_outer_exception(self, journal_home, team_name):
        """Returns empty list on unexpected exception (fail-open)."""
        from shared.session_journal import read_events

        with patch("shared.session_journal._journal_path", side_effect=RuntimeError("boom")):
            events = read_events()
            assert events == []

    def test_chronological_order(self, journal_home, team_name):
        """Events are returned in the order they were written."""
        from shared.session_journal import append_event, make_event, read_events

        for label in ["alpha", "beta", "gamma"]:
            append_event(make_event("test", label=label))

        events = read_events()
        assert [e["label"] for e in events] == ["alpha", "beta", "gamma"]

    def test_skips_truncated_line(self, journal_home, team_name, journal_file):
        """Truncated/incomplete JSON at end of file is skipped gracefully."""
        from shared.session_journal import append_event, make_event, read_events

        # Write two valid events
        append_event(make_event("test", seq=1))
        append_event(make_event("test", seq=2))

        # Append a truncated JSON line (simulates crash mid-write)
        with open(str(journal_file), "a") as f:
            f.write('{"v":1,"type":"test","seq":3,"ts"')  # no closing brace or newline

        events = read_events()
        assert len(events) == 2
        assert events[0]["seq"] == 1
        assert events[1]["seq"] == 2

    def test_returns_empty_for_nonexistent_event_type(self, journal_home, team_name):
        """Filtering by a type that no event matches returns an empty list."""
        from shared.session_journal import append_event, make_event, read_events

        append_event(make_event("alpha", data=1))
        append_event(make_event("beta", data=2))

        events = read_events(event_type="nonexistent_type")
        assert events == []


# ---------------------------------------------------------------------------
# read_last_event()
# ---------------------------------------------------------------------------


class TestReadLastEvent:
    """Tests for read_last_event() -- most recent event of a given type."""

    def test_returns_most_recent(self, journal_home, team_name):
        """P0: Returns the last matching event."""
        from shared.session_journal import append_event, make_event, read_last_event

        append_event(make_event("checkpoint", phase="PREPARE", data="old"))
        append_event(
            make_event(
                "agent_handoff",
                agent="coder",
                task_id="1",
                task_subject="s",
                handoff={},
            ),
        )
        append_event(make_event("checkpoint", phase="CODE", data="new"))

        last = read_last_event("checkpoint")
        assert last is not None
        assert last["data"] == "new"

    def test_returns_none_when_no_match(self, journal_home, team_name):
        """P0: Returns None when no events match the type."""
        from shared.session_journal import append_event, make_event, read_last_event

        append_event(make_event("session_start", team="t"))

        result = read_last_event("checkpoint")
        assert result is None

    def test_returns_none_for_missing_journal(self, journal_home, team_name):
        """P0: Returns None when journal file doesn't exist."""
        from shared.session_journal import read_last_event

        result = read_last_event("checkpoint")
        assert result is None


# ---------------------------------------------------------------------------
# get_journal_path()
# ---------------------------------------------------------------------------


class TestGetJournalPath:
    """Tests for get_journal_path() -- path string helper."""

    def test_returns_correct_path(self, journal_home, session_dir):
        from shared.session_journal import get_journal_path

        path = get_journal_path()
        expected = str(Path(session_dir) / "session-journal.jsonl")
        assert path == expected

    def test_does_not_require_file_existence(self, journal_home, session_dir):
        """Should return path even when file doesn't exist."""
        from shared.session_journal import get_journal_path

        path = get_journal_path()
        assert "session-journal.jsonl" in path
        assert not Path(path).exists()

    def test_returns_empty_string_when_session_dir_empty(self, journal_home, monkeypatch):
        """Returns empty string when _get_session_dir returns empty."""
        from shared.session_journal import get_journal_path
        import shared.session_journal as sj

        monkeypatch.setattr(sj, "_get_session_dir", lambda: "")

        assert get_journal_path() == ""


# ---------------------------------------------------------------------------
# read_events_from() — Explicit API
# ---------------------------------------------------------------------------


class TestReadEventsFrom:
    """Tests for read_events_from() — explicit session_dir parameter."""

    def test_reads_events_from_explicit_path(self, tmp_path):
        """P0: Reads events from a specified session directory."""
        from shared.session_journal import read_events_from

        session_dir = str(tmp_path / "sessions" / "proj" / "sess-abc")
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            '{"v":1,"type":"session_start","team":"t","ts":"2026-01-01T00:00:00Z"}\n'
            '{"v":1,"type":"agent_handoff","agent":"coder","ts":"2026-01-01T00:01:00Z"}\n'
        )

        events = read_events_from(session_dir)
        assert len(events) == 2
        assert events[0]["type"] == "session_start"
        assert events[1]["type"] == "agent_handoff"

    def test_filters_by_event_type(self, tmp_path):
        """P0: Filters events by type when event_type is specified."""
        from shared.session_journal import read_events_from

        session_dir = str(tmp_path / "sessions" / "proj" / "sess-abc")
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            '{"v":1,"type":"session_start","ts":"2026-01-01T00:00:00Z"}\n'
            '{"v":1,"type":"agent_handoff","agent":"a","ts":"2026-01-01T00:01:00Z"}\n'
            '{"v":1,"type":"agent_handoff","agent":"b","ts":"2026-01-01T00:02:00Z"}\n'
        )

        handoffs = read_events_from(session_dir, event_type="agent_handoff")
        assert len(handoffs) == 2
        assert handoffs[0]["agent"] == "a"
        assert handoffs[1]["agent"] == "b"

    def test_returns_empty_for_missing_journal(self, tmp_path):
        """P0: Returns empty list when journal file doesn't exist."""
        from shared.session_journal import read_events_from

        session_dir = str(tmp_path / "sessions" / "proj" / "sess-missing")

        assert read_events_from(session_dir) == []

    def test_returns_empty_for_empty_session_dir(self):
        """P0: Returns empty list when session_dir is empty string."""
        from shared.session_journal import read_events_from

        assert read_events_from("") == []

    def test_skips_malformed_lines(self, tmp_path):
        """P0: Malformed JSON lines are silently skipped."""
        from shared.session_journal import read_events_from

        session_dir = str(tmp_path / "sessions" / "proj" / "sess-abc")
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            '{"v":1,"type":"good","ts":"2026-01-01T00:00:00Z"}\n'
            'this is not json\n'
            '{"v":1,"type":"also_good","ts":"2026-01-01T00:01:00Z"}\n'
        )

        events = read_events_from(session_dir)
        assert len(events) == 2
        assert events[0]["type"] == "good"
        assert events[1]["type"] == "also_good"

    def test_skips_empty_lines(self, tmp_path):
        """Empty and whitespace-only lines are skipped."""
        from shared.session_journal import read_events_from

        session_dir = str(tmp_path / "sessions" / "proj" / "sess-abc")
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            '{"v":1,"type":"a","ts":"2026-01-01T00:00:00Z"}\n'
            '\n'
            '   \n'
            '{"v":1,"type":"b","ts":"2026-01-01T00:01:00Z"}\n'
        )

        events = read_events_from(session_dir)
        assert len(events) == 2

    def test_returns_empty_for_nonexistent_event_type(self, tmp_path):
        """Filtering by type that doesn't exist returns empty list."""
        from shared.session_journal import read_events_from

        session_dir = str(tmp_path / "sessions" / "proj" / "sess-abc")
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            '{"v":1,"type":"alpha","ts":"2026-01-01T00:00:00Z"}\n'
        )

        assert read_events_from(session_dir, event_type="nonexistent") == []


# ---------------------------------------------------------------------------
# read_last_event_from() — Explicit API
# ---------------------------------------------------------------------------


class TestReadLastEventFrom:
    """Tests for read_last_event_from() — explicit session_dir parameter."""

    def test_returns_most_recent_matching_event(self, tmp_path):
        """P0: Returns the last event matching the type."""
        from shared.session_journal import read_last_event_from

        session_dir = str(tmp_path / "sessions" / "proj" / "sess-abc")
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            '{"v":1,"type":"checkpoint","data":"old","ts":"2026-01-01T00:00:00Z"}\n'
            '{"v":1,"type":"agent_handoff","agent":"c","ts":"2026-01-01T00:01:00Z"}\n'
            '{"v":1,"type":"checkpoint","data":"new","ts":"2026-01-01T00:02:00Z"}\n'
        )

        result = read_last_event_from(session_dir, "checkpoint")
        assert result is not None
        assert result["data"] == "new"

    def test_returns_none_when_no_match(self, tmp_path):
        """P0: Returns None when no events match the type."""
        from shared.session_journal import read_last_event_from

        session_dir = str(tmp_path / "sessions" / "proj" / "sess-abc")
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            '{"v":1,"type":"session_start","ts":"2026-01-01T00:00:00Z"}\n'
        )

        assert read_last_event_from(session_dir, "nonexistent") is None

    def test_returns_none_for_missing_journal(self, tmp_path):
        """P0: Returns None when journal file doesn't exist."""
        from shared.session_journal import read_last_event_from

        session_dir = str(tmp_path / "sessions" / "proj" / "sess-missing")

        assert read_last_event_from(session_dir, "any") is None

    def test_returns_none_for_empty_session_dir(self):
        """P0: Returns None when session_dir is empty string."""
        from shared.session_journal import read_last_event_from

        assert read_last_event_from("", "any") is None

    def test_skips_malformed_lines(self, tmp_path):
        """Malformed lines are skipped; valid match still found."""
        from shared.session_journal import read_last_event_from

        session_dir = str(tmp_path / "sessions" / "proj" / "sess-abc")
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            '{"v":1,"type":"target","n":1,"ts":"2026-01-01T00:00:00Z"}\n'
            'not json\n'
            '{"v":1,"type":"target","n":2,"ts":"2026-01-01T00:01:00Z"}\n'
        )

        result = read_last_event_from(session_dir, "target")
        assert result is not None
        assert result["n"] == 2


# ---------------------------------------------------------------------------
# _get_session_dir() — Internal helper
# ---------------------------------------------------------------------------


class TestGetSessionDir:
    """Tests for _get_session_dir() — lazy import of pact_context."""

    def test_returns_session_dir_from_mock(self, session_dir):
        """Returns the session dir value from the monkeypatched function."""
        import shared.session_journal as sj

        result = sj._get_session_dir()
        assert result == session_dir

    def test_returns_empty_when_unavailable(self, monkeypatch):
        """Returns empty string when get_session_dir is unavailable."""
        import shared.session_journal as sj

        monkeypatch.setattr(sj, "_get_session_dir", lambda: "")
        assert sj._get_session_dir() == ""


# ---------------------------------------------------------------------------
# Integration: pact_context.write_context() -> journal (Scenario 13)
# ---------------------------------------------------------------------------


class TestWriteContextCacheIntegration:
    """Integration test: write_context() populates _cache so get_session_dir()
    works immediately after, enabling append_event() to find the journal path.

    This verifies the critical one-line fix in pact_context.py (Phase 2 of plan).
    """

    def test_full_path_chain_write_context_then_journal(self, tmp_path, monkeypatch):
        """P1: Real pact_context.write_context -> append_event -> file at sessions/ path."""
        import shared.pact_context as pc

        # Reset pact_context module state
        monkeypatch.setattr(pc, "_context_path", None)
        monkeypatch.setattr(pc, "_cache", None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        project_dir = str(tmp_path / "project")
        team_name = "pact-aaaaaaaa"

        # Write context — this should populate _cache
        pc.write_context(team_name, session_id, project_dir)

        # Verify _cache was populated (the fix)
        assert pc._cache is not None
        assert pc._cache["session_id"] == session_id

        # Verify get_session_dir returns the correct path
        sd = pc.get_session_dir()
        assert sd != ""
        assert "pact-sessions" in sd
        assert session_id in sd

        # Now use session_journal with the real get_session_dir
        import shared.session_journal as sj

        # Undo the autouse mock — use real _get_session_dir
        monkeypatch.setattr(sj, "_get_session_dir", pc.get_session_dir)

        from shared.session_journal import append_event, make_event, read_events

        event = make_event("integration_test", data="hello")
        result = append_event(event)
        assert result is True

        # Read back and verify
        events = read_events()
        assert len(events) == 1
        assert events[0]["type"] == "integration_test"
        assert events[0]["data"] == "hello"

        # Verify the file is at the expected sessions/ path
        expected_journal = (
            tmp_path / ".claude" / "pact-sessions" / "project" / session_id
            / "session-journal.jsonl"
        )
        assert expected_journal.exists()

    def test_write_context_without_cache_fix_would_fail(self, tmp_path, monkeypatch):
        """Verify that get_session_dir needs _cache populated by write_context.

        If _cache were None after write_context (pre-fix state), get_session_dir()
        would need to read from disk — which works, but the _cache optimization
        ensures it works even before get_pact_context() is called.
        """
        import shared.pact_context as pc

        monkeypatch.setattr(pc, "_context_path", None)
        monkeypatch.setattr(pc, "_cache", None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        session_id = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
        project_dir = str(tmp_path / "myproject")

        pc.write_context("pact-test", session_id, project_dir)

        # _cache should be set (the fix ensures this)
        assert pc._cache is not None
        assert pc.get_session_dir() != ""


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
                    result = append_event(event)
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

        events = read_events()
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
            task_subject="CODE: large payload",
            handoff=large_handoff,
        )

        # Verify the event is indeed large
        serialized = json.dumps(event)
        assert len(serialized) > 2000, f"Event only {len(serialized)} bytes, expected >2KB"

        result = append_event(event)
        assert result is True

        events = read_events()
        assert len(events) == 1
        assert events[0]["handoff"]["produced"] == large_handoff["produced"]
        assert len(events[0]["handoff"]["decisions"]) == 20


# ---------------------------------------------------------------------------
# Integration: _build_journal_resume()
# ---------------------------------------------------------------------------


class TestBuildJournalResume:
    """Integration tests for _build_journal_resume() in session_resume.py."""

    def test_returns_none_for_empty_journal(self, journal_home, session_dir):
        """Returns None when no events exist in the journal."""
        from shared.session_resume import _build_journal_resume

        result = _build_journal_resume(session_dir)
        assert result is None

    def test_returns_none_for_nonexistent_session_dir(self, journal_home):
        """Returns None for a session dir with no journal file."""
        from shared.session_resume import _build_journal_resume

        result = _build_journal_resume("nonexistent-session-dir")
        assert result is None

    def test_includes_handoff_summary(self, journal_home, session_dir):
        """Produces resume with agent handoffs and first decision."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        append_event(
            make_event(
                "agent_handoff",
                agent="backend-coder",
                task_id="1",
                task_subject="CODE: implement auth",
                handoff={"decisions": ["Used JWT for token-based auth"]},
            ),
        )

        result = _build_journal_resume(session_dir)
        assert result is not None
        assert "Previous session summary" in result
        assert "journal" in result
        assert "## Completed Work" in result
        assert "backend-coder" in result
        assert "CODE: implement auth" in result
        assert "Used JWT" in result

    def test_truncates_long_decisions(self, journal_home, session_dir):
        """Decision summaries longer than 80 chars are truncated to 77+..."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        long_decision = "A" * 100

        append_event(
            make_event(
                "agent_handoff",
                agent="coder",
                task_id="1",
                task_subject="CODE: test",
                handoff={"decisions": [long_decision]},
            ),
        )

        result = _build_journal_resume(session_dir)
        assert result is not None
        # Should be truncated to 77 chars + "..."
        assert "A" * 77 + "..." in result
        assert "A" * 100 not in result

    def test_includes_phase_progress(self, journal_home, session_dir):
        """Includes completed and in-progress phases."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        append_event(
            make_event("phase_transition", phase="PREPARE", status="completed"),
        )
        append_event(
            make_event("phase_transition", phase="ARCHITECT", status="completed"),
        )
        append_event(
            make_event("phase_transition", phase="CODE", status="started"),
        )

        result = _build_journal_resume(session_dir)
        assert result is not None
        assert "Completed phases: PREPARE, ARCHITECT" in result
        assert "Last active phase: CODE" in result

    def test_includes_session_end_warnings(self, journal_home, session_dir):
        """Includes warnings from session_end events."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        append_event(make_event("session_start", team="pact-test1234"))
        append_event(
            make_event(
                "session_end",
                warning="Session ended without memory consolidation. PR #42 is open.",
            ),
        )

        result = _build_journal_resume(session_dir)
        assert result is not None
        assert "**Warning**" in result
        assert "PR #42" in result

    def test_handoff_without_decisions(self, journal_home, session_dir):
        """Handoff with no decisions should still appear, just without summary."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        append_event(
            make_event(
                "agent_handoff",
                agent="preparer",
                task_id="1",
                task_subject="PREPARE: research",
                handoff={"produced": ["docs/prep.md"]},
            ),
        )

        result = _build_journal_resume(session_dir)
        assert result is not None
        assert "preparer: PREPARE: research" in result

    def test_multiple_handoffs(self, journal_home, session_dir):
        """Multiple agent handoffs are all listed."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        for idx, agent in enumerate(["preparer", "architect", "backend-coder"]):
            append_event(
                make_event(
                    "agent_handoff",
                    agent=agent,
                    task_id=str(idx + 1),
                    task_subject=f"Task for {agent}",
                    handoff={"decisions": [f"{agent} decision"]},
                ),
            )

        result = _build_journal_resume(session_dir)
        assert result is not None
        assert "preparer" in result
        assert "architect" in result
        assert "backend-coder" in result


# ---------------------------------------------------------------------------
# Integration: _check_journal_paused_state()
# ---------------------------------------------------------------------------


class TestCheckJournalPausedState:
    """Integration tests for _check_journal_paused_state() in session_resume.py."""

    def test_returns_none_when_no_paused_events(self, journal_home, session_dir):
        """Returns None when no session_paused events exist."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        # Write some non-paused events
        append_event(make_event("session_start", team="pact-test1234"))

        result = _check_journal_paused_state(session_dir)
        assert result is None

    def test_returns_none_for_nonexistent_session_dir(self, journal_home):
        """Returns None for a session dir with no journal."""
        from shared.session_resume import _check_journal_paused_state

        result = _check_journal_paused_state("nonexistent-session-dir")
        assert result is None

    def test_returns_formatted_context(self, journal_home, session_dir):
        """Returns formatted paused work context with PR details."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        append_event(
            make_event(
                "session_paused",
                pr_number=42,
                pr_url="https://github.com/owner/repo/pull/42",
                branch="feat/my-feature",
                worktree_path="/Users/dev/project/.worktrees/feat/my-feature",
                consolidation_completed=True,
            ),
        )

        with patch("shared.session_resume._check_pr_state", return_value="OPEN"):
            result = _check_journal_paused_state(session_dir)

        assert result is not None
        assert "PR #42" in result
        assert "feat/my-feature" in result
        assert "Paused work detected" in result

    def test_includes_consolidation_warning(self, journal_home, session_dir):
        """Includes memory consolidation warning when not completed."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        append_event(
            make_event(
                "session_paused",
                pr_number=99,
                pr_url="https://github.com/owner/repo/pull/99",
                branch="feat/test",
                worktree_path="/tmp/wt",
                consolidation_completed=False,
            ),
        )

        with patch("shared.session_resume._check_pr_state", return_value="OPEN"):
            result = _check_journal_paused_state(session_dir)

        assert result is not None
        assert "Memory consolidation did NOT complete" in result

    def test_no_consolidation_warning_when_completed(self, journal_home, session_dir):
        """No consolidation warning when consolidation_completed is True."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        append_event(
            make_event(
                "session_paused",
                pr_number=99,
                pr_url="https://github.com/owner/repo/pull/99",
                branch="feat/test",
                worktree_path="/tmp/wt",
                consolidation_completed=True,
            ),
        )

        with patch("shared.session_resume._check_pr_state", return_value="OPEN"):
            result = _check_journal_paused_state(session_dir)

        assert result is not None
        assert "consolidation" not in result.lower()

    def test_returns_none_when_pr_number_is_none(self, journal_home, session_dir):
        """Returns None when the paused event has no pr_number."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        append_event(
            make_event(
                "session_paused",
                branch="feat/test",
                worktree_path="/tmp/wt",
            ),
        )

        result = _check_journal_paused_state(session_dir)
        assert result is None

    def test_stale_event_older_than_14_days(self, journal_home, session_dir, journal_file):
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

        result = _check_journal_paused_state(session_dir)
        assert result is not None
        assert "Stale" in result or "older than 14 days" in result
        assert "PR #55" in result

    def test_merged_pr_returns_info(self, journal_home, session_dir):
        """Returns informational message when PR is MERGED."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        append_event(
            make_event(
                "session_paused",
                pr_number=77,
                pr_url="https://github.com/owner/repo/pull/77",
                branch="feat/done",
                worktree_path="/tmp/done",
                consolidation_completed=True,
            ),
        )

        with patch("shared.session_resume._check_pr_state", return_value="MERGED"):
            result = _check_journal_paused_state(session_dir)

        assert result is not None
        assert "merged" in result.lower()
        assert "PR #77" in result

    def test_closed_pr_returns_info(self, journal_home, session_dir):
        """Returns informational message when PR is CLOSED."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import _check_journal_paused_state

        append_event(
            make_event(
                "session_paused",
                pr_number=88,
                pr_url="https://github.com/owner/repo/pull/88",
                branch="feat/abandoned",
                worktree_path="/tmp/abandoned",
                consolidation_completed=True,
            ),
        )

        with patch("shared.session_resume._check_pr_state", return_value="CLOSED"):
            result = _check_journal_paused_state(session_dir)

        assert result is not None
        assert "closed" in result.lower()

    def test_unparseable_ts_does_not_crash(self, journal_home, session_dir, journal_file):
        """M5: corrupted `ts` field is swallowed, function falls through to PR check.

        The TTL gate at session_resume.py:358-369 catches
        (ValueError, TypeError, OverflowError) from datetime.fromisoformat(...)
        and pass-throughs to the active-PR check. With PR state mocked to OPEN,
        the standard 'Paused work detected' message must be returned -- the
        unparseable ts must not bubble up as an exception or short-circuit to
        None.
        """
        from shared.session_resume import _check_journal_paused_state

        # Write a paused event directly (bypasses make_event so we control ts).
        event = {
            "v": 1,
            "type": "session_paused",
            "pr_number": 123,
            "branch": "feat/bad-ts",
            "worktree_path": "/tmp/bad-ts",
            "consolidation_completed": True,
            "ts": "not-a-timestamp",  # corrupted -- fromisoformat will raise ValueError
        }
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        journal_file.write_text(json.dumps(event) + "\n", encoding="utf-8")

        with patch("shared.session_resume._check_pr_state", return_value="OPEN"):
            result = _check_journal_paused_state(session_dir)

        assert result is not None
        assert "Paused work detected" in result
        assert "PR #123" in result
        assert "feat/bad-ts" in result
        # Stale TTL message should NOT appear -- we never reached the comparison
        assert "Stale" not in result
        assert "older than 14 days" not in result

    def test_missing_ts_does_not_crash(self, journal_home, session_dir, journal_file):
        """M5: missing `ts` field is treated as fail-open (skips TTL gate).

        When the `ts` field is absent (empty string from .get default), the
        `if ts_str:` guard at session_resume.py:358 short-circuits past the
        TTL block entirely and falls through to the PR check. With PR mocked
        to OPEN, the standard paused message is returned.
        """
        from shared.session_resume import _check_journal_paused_state

        # No `ts` key at all -- event.get("ts", "") returns "" -> guard skips block.
        event = {
            "v": 1,
            "type": "session_paused",
            "pr_number": 456,
            "branch": "feat/no-ts",
            "worktree_path": "/tmp/no-ts",
            "consolidation_completed": True,
        }
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        journal_file.write_text(json.dumps(event) + "\n", encoding="utf-8")

        with patch("shared.session_resume._check_pr_state", return_value="OPEN"):
            result = _check_journal_paused_state(session_dir)

        assert result is not None
        assert "Paused work detected" in result
        assert "PR #456" in result


# ---------------------------------------------------------------------------
# Integration: restore_last_session() and check_paused_state() prefer journal
# ---------------------------------------------------------------------------


class TestJournalPreference:
    """Tests that restore_last_session and check_paused_state use journal."""

    def test_restore_uses_journal(self, journal_home, session_dir):
        """restore_last_session reads journal when prev_session_dir is provided."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import restore_last_session

        append_event(
            make_event(
                "agent_handoff",
                agent="coder",
                task_id="1",
                task_subject="CODE: feature",
                handoff={"decisions": ["Built feature X"]},
            ),
        )

        result = restore_last_session(prev_session_dir=session_dir)

        assert result is not None
        assert "journal" in result  # Journal-based resume header

    def test_restore_returns_none_without_team_name(self, journal_home):
        """restore_last_session returns None when no prev_session_dir."""
        from shared.session_resume import restore_last_session

        result = restore_last_session(prev_session_dir=None)
        assert result is None

    def test_check_paused_uses_journal(self, journal_home, session_dir):
        """check_paused_state reads journal when prev_session_dir is provided."""
        from shared.session_journal import append_event, make_event
        from shared.session_resume import check_paused_state

        append_event(
            make_event(
                "session_paused",
                pr_number=42,
                pr_url="https://github.com/owner/repo/pull/42",
                branch="feat/test",
                worktree_path="/tmp/wt",
                consolidation_completed=True,
            ),
        )

        with patch("shared.session_resume._check_pr_state", return_value="OPEN"):
            result = check_paused_state(prev_session_dir=session_dir)

        assert result is not None
        assert "PR #42" in result

    def test_check_paused_returns_none_without_team_name(self, journal_home):
        """check_paused_state returns None when no prev_session_dir."""
        from shared.session_resume import check_paused_state

        result = check_paused_state(prev_session_dir=None)
        assert result is None


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

        events = read_events("session_start")
        assert len(events) >= 1
        assert events[0]["team"] == "pact-abc12345"


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
        append_event(make_event("session_start", team="pact-endtest12"))

        input_data = {}

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("session_end.pact_context") as mock_ctx, \
             patch("session_end.get_project_dir", return_value=str(journal_home / "project")), \
             patch("session_end.get_session_dir", return_value=None), \
             patch("session_end.get_session_id", return_value="test-session-id"), \
             patch("session_end.get_team_name", return_value="pact-endtest12"), \
             patch("session_end.get_task_list", return_value=[]):

            with pytest.raises(SystemExit) as exc:
                from session_end import main
                main()

            assert exc.value.code == 0

        # Verify session_end event was written
        from shared.session_journal import read_events

        events = read_events("session_end")
        assert len(events) >= 1


# ---------------------------------------------------------------------------
# Integration: _extract_prev_session_dir
# ---------------------------------------------------------------------------


class TestExtractPrevSessionDir:
    """Tests for session_init._extract_prev_session_dir()."""

    def test_extracts_session_dir_from_claude_md(self, tmp_path):
        """Extracts session dir from Session dir line in Current Session block."""
        from session_init import _extract_prev_session_dir

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n"
            "## Current Session\n"
            "- Resume: `claude --resume abc123`\n"
            "- Team: `pact-abc12345`\n"
            "- Session dir: `~/.claude/pact-sessions/myproject/abc123`\n"
            "- Started: 2026-04-05\n"
        )

        result = _extract_prev_session_dir(str(tmp_path))
        assert result is not None
        assert result.endswith("pact-sessions/myproject/abc123")

    def test_fallback_derives_from_resume_line(self, tmp_path):
        """Falls back to deriving session dir from Resume line + project slug."""
        from session_init import _extract_prev_session_dir

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n"
            "## Current Session\n"
            "- Resume: `claude --resume abc12345-6789-0123-4567-890123456789`\n"
            "- Team: `pact-abc12345`\n"
            "- Started: 2026-04-05\n"
        )

        result = _extract_prev_session_dir(str(tmp_path))
        assert result is not None
        slug = tmp_path.name  # project slug = basename of project_dir
        assert slug in result
        assert "abc12345-6789-0123-4567-890123456789" in result

    def test_returns_none_when_no_claude_md(self, tmp_path):
        """Returns None when CLAUDE.md doesn't exist."""
        from session_init import _extract_prev_session_dir

        result = _extract_prev_session_dir(str(tmp_path))
        assert result is None

    def test_returns_none_when_no_session_info(self, tmp_path):
        """Returns None when CLAUDE.md has no session info lines."""
        from session_init import _extract_prev_session_dir

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\nNo session info here.\n")

        result = _extract_prev_session_dir(str(tmp_path))
        assert result is None

    def test_returns_none_for_empty_project_dir(self):
        """Returns None when project_dir is empty string."""
        from session_init import _extract_prev_session_dir

        result = _extract_prev_session_dir("")
        assert result is None

    def test_returns_none_for_none_project_dir(self):
        """Returns None when project_dir is None."""
        from session_init import _extract_prev_session_dir

        result = _extract_prev_session_dir(None)  # type: ignore[arg-type]
        assert result is None

    def test_returns_none_on_ioerror(self, tmp_path):
        """Returns None when CLAUDE.md read raises IOError (fail-open)."""
        from unittest.mock import patch as mock_patch
        from session_init import _extract_prev_session_dir

        # Create CLAUDE.md so the existence check passes
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n## Current Session\n"
            "- Session dir: `~/.claude/pact-sessions/proj/abc123`\n"
        )

        # Patch read_text to raise IOError
        with mock_patch.object(Path, "read_text", side_effect=IOError("disk error")):
            result = _extract_prev_session_dir(str(tmp_path))

        assert result is None

    def test_non_tilde_absolute_path_returned_as_is(self, tmp_path):
        """Absolute path without tilde prefix is returned unchanged."""
        from session_init import _extract_prev_session_dir

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n"
            "## Current Session\n"
            "- Resume: `claude --resume abc123`\n"
            "- Session dir: `/var/data/pact-sessions/myproject/abc123`\n"
            "- Started: 2026-04-05\n"
        )

        result = _extract_prev_session_dir(str(tmp_path))
        assert result == "/var/data/pact-sessions/myproject/abc123"


# ---------------------------------------------------------------------------
# CLI tests (main() via subprocess)
# ---------------------------------------------------------------------------

# Resolve absolute path to session_journal.py once.
_SJ_SCRIPT = str(
    Path(__file__).parent.parent / "hooks" / "shared" / "session_journal.py"
)


class TestCLI:
    """Tests for the CLI entry point (main) invoked via subprocess."""

    def test_write_creates_event_and_exits_0(self, journal_home, session_dir, journal_file):
        """44. write subcommand creates event and exits 0."""
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--type", "test_event",
                "--session-dir", session_dir,
                "--data", '{"key": "value"}',
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 0, result.stderr

        # Verify the event was written
        assert journal_file.exists()
        event = json.loads(journal_file.read_text().strip())
        assert event["type"] == "test_event"
        assert event["key"] == "value"
        assert event["v"] == 1
        assert "ts" in event

    def test_read_outputs_json_array_and_exits_0(self, journal_home, session_dir, journal_file):
        """45. read subcommand outputs JSON array and exits 0."""
        # Seed two events
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        journal_file.write_text(
            '{"v":1,"type":"a","ts":"2026-01-01T00:00:00Z"}\n'
            '{"v":1,"type":"b","ts":"2026-01-01T00:01:00Z"}\n'
        )

        result = subprocess.run(
            [sys.executable, _SJ_SCRIPT, "read", "--session-dir", session_dir],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 0
        events = json.loads(result.stdout)
        assert len(events) == 2
        assert events[0]["type"] == "a"
        assert events[1]["type"] == "b"

    def test_read_with_type_filter(self, journal_home, session_dir, journal_file):
        """45b. read --type filters to matching events only."""
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        journal_file.write_text(
            '{"v":1,"type":"a","ts":"2026-01-01T00:00:00Z"}\n'
            '{"v":1,"type":"b","ts":"2026-01-01T00:01:00Z"}\n'
        )

        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "read",
                "--session-dir", session_dir, "--type", "b",
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 0
        events = json.loads(result.stdout)
        assert len(events) == 1
        assert events[0]["type"] == "b"

    def test_read_last_outputs_single_event(self, journal_home, session_dir, journal_file):
        """46. read-last subcommand outputs single event JSON and exits 0."""
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        journal_file.write_text(
            '{"v":1,"type":"x","n":1,"ts":"2026-01-01T00:00:00Z"}\n'
            '{"v":1,"type":"x","n":2,"ts":"2026-01-01T00:01:00Z"}\n'
        )

        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "read-last",
                "--session-dir", session_dir, "--type", "x",
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 0
        event = json.loads(result.stdout)
        assert event["n"] == 2

    def test_write_invalid_data_json_exits_1(self, journal_home, session_dir):
        """47. write with invalid --data JSON exits 1."""
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--type", "test_event",
                "--session-dir", session_dir,
                "--data", "not-json",
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 1
        assert "invalid --data JSON" in result.stderr

    def test_write_missing_type_exits_nonzero(self, journal_home):
        """48. write with missing --type exits non-zero."""
        result = subprocess.run(
            [sys.executable, _SJ_SCRIPT, "write", "--session-dir", "t"],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode != 0

    def test_read_last_no_match_outputs_null(self, journal_home, session_dir, journal_file):
        """49. read-last with no matching events outputs 'null'."""
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        journal_file.write_text(
            '{"v":1,"type":"a","ts":"2026-01-01T00:00:00Z"}\n'
        )

        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "read-last",
                "--session-dir", session_dir, "--type", "nonexistent",
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "null"

    def test_write_non_dict_data_exits_1(self, journal_home, session_dir, journal_file):
        """50. write with non-dict --data (array) exits 1 with error."""
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--session-dir", session_dir, "--type", "test",
                "--data", "[1,2,3]",
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 1
        assert "must be a JSON object" in result.stderr

    def test_write_bool_v_field_rejected(self, journal_home, session_dir, journal_file):
        """51. write with --data containing bool 'v' is rejected (M2 fix).

        Regression: a caller passing --data '{"v": true, ...}' would have
        overwritten the default v=1 (set by make_event) with a bool, which
        bypasses the int-not-bool check that append_event() enforces.
        The CLI write path must apply the same schema validation.
        """
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--type", "test_event",
                "--session-dir", session_dir,
                "--data", '{"v": true}',
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode != 0
        assert "invalid event schema" in result.stderr
        # The reason propagation lands the exact failing check in stderr
        # instead of the pre-refactor generic "(v must be int, type must
        # be non-empty str)" line that mentioned both checks regardless of
        # which one fired.
        assert "v must be int" in result.stderr
        # The journal must NOT have been written.
        assert not journal_file.exists() or journal_file.read_text() == ""

    def test_write_cli_surfaces_missing_field_reason(
        self, journal_home, session_dir, journal_file,
    ):
        """B2/RG1/A4: CLI write reports the *precise* per-type failure.

        Before the reason-propagation fix, this path printed the generic
        "v must be int, type must be non-empty str" message even when a
        per-type required field was missing. The regression test pins the
        new behavior: the stderr line names the specific missing field and
        the event type, so operators can act on it without re-running with
        a debugger. Uses `phase_transition` (the BugF1 canary type) with
        `status` present but `phase` missing.
        """
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--type", "phase_transition",
                "--session-dir", session_dir,
                "--data", '{"status": "started"}',
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 1
        assert "invalid event schema" in result.stderr
        # The specific failing field and event type must be in the message.
        assert "phase" in result.stderr
        assert "phase_transition" in result.stderr
        # And the stale generic phrase from before the fix must NOT appear.
        assert "v must be int" not in result.stderr
        # Journal must not have been written.
        assert not journal_file.exists() or journal_file.read_text() == ""

    def test_write_rejects_empty_session_dir(self, journal_home, tmp_path):
        """AdvF4: CLI write rejects an empty --session-dir with exit 1.

        Regression: previously an empty string for --session-dir slipped past
        argparse's `required=True` (which only checks presence, not value),
        fell into `_journal_path_from("")`, and silently wrote
        `./session-journal.jsonl` into the caller's current working directory.
        The CLI now mirrors read_events_from / read_last_event_from by
        rejecting the empty-string case before touching the filesystem.

        Matches the behavior at session_journal.py read_events_from:273 and
        read_last_event_from:349 which return empty collections on empty
        session_dir without performing any I/O.
        """
        # Run in an isolated cwd so we can assert that no stray journal file
        # is created there. `tmp_path` is the ideal scratch directory.
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--type", "test_event",
                "--session-dir", "",
                "--data", "{}",
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
            cwd=str(tmp_path),
        )
        assert result.returncode == 1
        assert "--session-dir must be non-empty" in result.stderr
        # CRITICAL: no stray journal must have been created in cwd.
        assert not (tmp_path / "session-journal.jsonl").exists()

    def test_write_rejects_whitespace_only_type_via_cli(
        self, journal_home, session_dir, journal_file,
    ):
        """A2: CLI write rejects --type '   ' (whitespace-only).

        Mirrors the baseline unit test
        (test_baseline_type_check_rejects_whitespace_only) but exercises the
        full CLI write path so we catch any call site that bypasses
        _validate_event_schema and trusts argparse's `required=True` alone.
        """
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--type", "   ",  # Whitespace-only
                "--session-dir", session_dir,
                "--data", "{}",
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 1
        assert "invalid event schema" in result.stderr
        assert "type must be non-empty str" in result.stderr
        assert not journal_file.exists() or journal_file.read_text() == ""


# ---------------------------------------------------------------------------
# Per-type schema validation (BugF1 primary defense — validator at write time)
# ---------------------------------------------------------------------------


class TestValidateEventSchemaPerType:
    """Per-type schema validation tests for BugF1 fix.

    Every event type in _REQUIRED_FIELDS_BY_TYPE must pass validation when
    all required fields are present, and must be rejected when any single
    required field is missing. This class is the bulwark that prevents
    malformed events from reaching disk where _build_journal_resume would
    have to defend against them at read time.

    The dict _REQUIRED_FIELDS_BY_TYPE is the authoritative schema; tests
    here mirror it so adding a new event type requires a test to land with
    it (see the comment on _REQUIRED_FIELDS_BY_TYPE in session_journal.py).
    """

    # Known event types and one valid sample of required fields. Mirrors
    # _REQUIRED_FIELDS_BY_TYPE — when adding a new entry to the source dict,
    # add the matching sample here so the happy-path test and missing-field
    # test both cover it.
    _SAMPLES: dict = {
        "session_start": {
            "session_id": "test-session-id",
            "project_dir": "/tmp/proj",
        },
        "variety_assessed": {
            "task_id": "42",
            "variety": {"novelty": 1, "scope": 1, "uncertainty": 1, "risk": 1, "total": 4},
        },
        "phase_transition": {"phase": "CODE", "status": "started"},
        "checkpoint": {"phase": "CODE"},
        "agent_dispatch": {"agent": "coder", "task_id": "1", "phase": "CODE"},
        "agent_handoff": {
            "agent": "coder",
            "task_id": "1",
            "task_subject": "CODE: thing",
            "handoff": {"decisions": ["x"]},
        },
        "s2_state_seeded": {
            "worktree": "/tmp/wt",
            "agents": ["c1", "c2"],
            "boundaries": {"c1": ["a/"], "c2": ["b/"]},
        },
        "commit": {"sha": "abc123", "message": "fix: thing", "phase": "CODE"},
        "review_dispatch": {
            "pr_number": 42,
            "pr_url": "https://github.com/o/r/pull/42",
            "reviewers": ["r1"],
        },
        "review_finding": {
            "severity": "blocking",
            "finding": "missing X",
            "reviewer": "architect",
        },
        "remediation": {"cycle": 1, "items": ["F1"], "fixer": "coder"},
        "pr_ready": {
            "pr_number": 42,
            "pr_url": "https://github.com/o/r/pull/42",
            "commits": 7,
        },
        "session_paused": {
            "pr_number": 42,
            "pr_url": "https://github.com/o/r/pull/42",
            "branch": "feat/x",
            "worktree_path": "/tmp/wt",
            "consolidation_completed": True,
        },
        "session_end": {},  # No required fields; baseline-only.
    }

    def test_samples_mirror_required_fields_dict(self):
        """Meta-test: _SAMPLES must cover every key in _REQUIRED_FIELDS_BY_TYPE.

        If this test fails, a new event type was added to the source dict
        without adding a matching sample here — guards against the per-type
        check silently skipping coverage of new types.
        """
        from shared.session_journal import _REQUIRED_FIELDS_BY_TYPE

        assert set(self._SAMPLES.keys()) == set(_REQUIRED_FIELDS_BY_TYPE.keys())

    @pytest.mark.parametrize("event_type", list(_SAMPLES.keys()))
    def test_happy_path_all_required_fields_present(self, event_type):
        """Validation passes when all required fields are present.

        The validator returns a `(ok, reason)` tuple — on success the reason
        is the literal string "ok" so the CLI write path can print a meaningful
        message on the opposite branch without a special case for success.
        """
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event(event_type, **self._SAMPLES[event_type])
        ok, reason = _validate_event_schema(event)
        assert ok is True
        assert reason == "ok"

    @pytest.mark.parametrize(
        "event_type",
        [t for t, f in _SAMPLES.items() if f],  # Skip session_end (no required fields).
    )
    def test_missing_single_required_field_rejected(self, event_type):
        """Validation rejects when ANY single required field is missing.

        Iterates through each required field, removes it, and verifies the
        validator returns (False, "missing required field '<field>' for
        type '<event_type>'"). This catches schema drift where a writer
        stops passing a load-bearing field, AND pins the reason string
        format so the CLI write path can surface the precise field.
        """
        from shared.session_journal import _validate_event_schema, make_event

        sample = self._SAMPLES[event_type]
        for missing_field in sample:
            partial = {k: v for k, v in sample.items() if k != missing_field}
            event = make_event(event_type, **partial)
            ok, reason = _validate_event_schema(event)
            assert ok is False, (
                f"{event_type} with missing {missing_field!r} should be rejected"
            )
            expected = (
                f"missing required field '{missing_field}' "
                f"for type '{event_type}'"
            )
            assert reason == expected, (
                f"{event_type} missing {missing_field!r}: expected reason "
                f"{expected!r}, got {reason!r}"
            )

    @pytest.mark.parametrize(
        "event_type",
        [t for t, f in _SAMPLES.items() if f],
    )
    def test_none_required_field_rejected(self, event_type):
        """Validation rejects when a required field is present but None.

        The validator's check is `field not in event or event[field] is None`
        — this covers the explicit-None case. Without this guard a writer
        that passes `phase=None` would slip through. The reason string
        uses the same "missing required field" template so the None case
        and the missing-key case surface identically to the CLI user.
        """
        from shared.session_journal import _validate_event_schema, make_event

        sample = self._SAMPLES[event_type]
        for none_field in sample:
            with_none = {**sample, none_field: None}
            event = make_event(event_type, **with_none)
            ok, reason = _validate_event_schema(event)
            assert ok is False, (
                f"{event_type} with {none_field}=None should be rejected"
            )
            assert (
                f"missing required field '{none_field}' "
                f"for type '{event_type}'"
            ) == reason

    def test_unknown_event_type_passes_per_type(self):
        """Unknown event types pass per-type validation (opt-in whitelist).

        The validator's whitelist is opt-in — unknown event types bypass
        per-type checks so unit tests using made-up "test" types still
        work. Only baseline v/type/ts are enforced for unknowns.
        """
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event("some_unit_test_type_not_in_dict", payload="anything")
        ok, reason = _validate_event_schema(event)
        assert ok is True
        assert reason == "ok"

    @pytest.mark.parametrize(
        "bad_v, expected_reason",
        [
            (None, "v must be int"),
            ("1", "v must be int"),
            (True, "v must be int"),    # bool is a subclass of int — rejected.
            (False, "v must be int"),
        ],
        ids=["missing_v", "string_v", "bool_true_v", "bool_false_v"],
    )
    def test_baseline_v_check_reason(self, bad_v, expected_reason):
        """Baseline v-field check surfaces the 'v must be int' reason."""
        from shared.session_journal import _validate_event_schema

        event: dict = {"type": "session_end", "ts": "2026-01-01T00:00:00Z"}
        if bad_v is not None:
            event["v"] = bad_v
        ok, reason = _validate_event_schema(event)
        assert ok is False
        assert reason == expected_reason

    @pytest.mark.parametrize(
        "bad_type",
        ["", " ", "\t", "\n", "   \t  "],
        ids=["empty", "single_space", "tab", "newline", "mixed_whitespace"],
    )
    def test_baseline_type_check_rejects_whitespace_only(self, bad_type):
        """A2: whitespace-only --type is rejected with 'type must be non-empty str'.

        Prior to this fix the validator only checked `len(event_type) > 0`,
        so " " or "\\t" slipped through and produced a journal line with a
        whitespace-only type. The validator now strips before checking and
        returns the same reason whether the field is missing, non-string,
        empty, or whitespace-only.
        """
        from shared.session_journal import _validate_event_schema

        event = {"v": 1, "type": bad_type, "ts": "2026-01-01T00:00:00Z"}
        ok, reason = _validate_event_schema(event)
        assert ok is False
        assert reason == "type must be non-empty str"

    def test_append_event_rejects_missing_required_field(self, journal_home):
        """append_event returns False (fail-open) when required field missing.

        End-to-end check that the write path enforces per-type schema — the
        event never lands on disk. Uses `phase_transition` (the BugF1 crash
        site) as the canary.
        """
        from shared.session_journal import append_event, make_event

        # Missing `phase` — the field _build_journal_resume subscripts.
        bad_event = make_event("phase_transition", status="started")
        assert append_event(bad_event) is False

    def test_cli_write_rejects_missing_required_field(
        self, journal_home, session_dir, journal_file,
    ):
        """CLI write path exits 1 and writes stderr on missing required field.

        Dual-API check: validation must fire in both append_event (hooks)
        and the CLI write subcommand (orchestrator commands). This test
        catches a regression where only one path applied per-type checks.
        """
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--type", "phase_transition",
                "--session-dir", session_dir,
                "--data", '{"status": "started"}',  # missing `phase`
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 1
        assert "invalid event schema" in result.stderr
        # Journal must not have been written.
        assert not journal_file.exists() or journal_file.read_text() == ""
