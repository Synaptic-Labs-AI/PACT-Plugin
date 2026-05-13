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
import multiprocessing
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# Module-level worker for multi-process concurrency tests.
# Must be picklable (i.e. top-level) so multiprocessing.Process can spawn it
# on macOS where the default start method is "spawn" (not "fork").
def _concurrent_large_writer_worker(
    worker_id: int,
    num_events: int,
    payload_size: int,
    journal_path: str,
    hooks_path: str,
) -> None:
    import sys as _sys
    if hooks_path not in _sys.path:
        _sys.path.insert(0, hooks_path)
    from pathlib import Path as _Path
    from shared.session_journal import _atomic_write

    # Each event is a JSON-shaped line well above macOS PIPE_BUF (512 B).
    # Use a distinctive payload the reader can validate per-event.
    filler = "x" * max(0, payload_size - 200)
    for seq in range(num_events):
        event = {
            "v": 1,
            "type": "test",
            "ts": "2026-04-06T00:00:00Z",
            "worker": worker_id,
            "seq": seq,
            "filler": filler,
        }
        line = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
        # Guardrail: the test is only meaningful above PIPE_BUF. If the
        # filler calculation is ever tuned wrong, fail loudly in the worker.
        if len(line) <= 512:
            raise AssertionError(
                f"worker {worker_id}: line too small "
                f"({len(line)} bytes) — test would not exercise "
                f"the PIPE_BUF interleaving window"
            )
        if not _atomic_write(_Path(journal_path), line):
            raise RuntimeError(
                f"worker {worker_id} seq {seq}: _atomic_write returned False"
            )


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

    def test_caller_ts_is_honored(self):
        """A caller-supplied ts in kwargs is preserved (setdefault semantics).

        Previous behavior unconditionally overwrote any caller ts, which
        contradicted the docstring claim that ts is only auto-set when
        missing. The new contract honors a caller-supplied ts so test
        fixtures and backfill tooling can stamp deterministic timestamps.
        """
        from shared.session_journal import make_event

        event = make_event("test", ts="2026-04-06T12:00:00Z")
        assert event["ts"] == "2026-04-06T12:00:00Z"

    def test_ts_auto_set_when_caller_omits(self):
        """When the caller does not supply ts, make_event fills it in."""
        from shared.session_journal import make_event

        event = make_event("test")
        # ts should be a valid ISO 8601 UTC string
        assert event["ts"].endswith("Z")
        # And it must look like the documented format
        assert "T" in event["ts"]


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

    def test_invalid_utf8_does_not_drop_whole_file(
        self, journal_home, team_name, journal_file,
    ):
        """A bad byte on one line must not hide every other event.

        Previously `_read_events_at` used `path.read_text(encoding="utf-8")`,
        which raises UnicodeDecodeError on a single invalid byte. The
        outer `except Exception: return []` then dropped the *entire*
        file. Linked to the flock fix: if concurrency (or anything else)
        ever produces a malformed byte range, we must still surface the
        surrounding valid events so the journal stays recoverable.

        The fix switches to `errors="replace"` so invalid bytes become
        U+FFFD, the bad line fails `json.loads` (and is skipped), and
        every other event is still returned.
        """
        from shared.session_journal import read_events

        # Write two valid JSON lines sandwiching an invalid UTF-8 byte
        # sequence. Use raw bytes because Python's text mode would refuse
        # to encode the invalid bytes.
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        valid1 = b'{"v":1,"type":"test","seq":1,"ts":"2026-01-01T00:00:00Z"}\n'
        bad_line = b"\xff\xfe garbage bytes that are not valid utf-8\n"
        valid2 = b'{"v":1,"type":"test","seq":2,"ts":"2026-01-01T00:00:00Z"}\n'
        journal_file.write_bytes(valid1 + bad_line + valid2)

        events = read_events()

        # Both valid events survive; the invalid-byte line is silently
        # dropped (json.loads rejects it after errors="replace" leaves
        # U+FFFD in place of the bad bytes).
        assert len(events) == 2, (
            f"expected 2 surviving events, got {len(events)}"
        )
        assert events[0]["seq"] == 1
        assert events[1]["seq"] == 2


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

    def test_atomic_write_holds_exclusive_lock_around_write_loop(
        self, tmp_path, monkeypatch
    ):
        """P0 (mutation test): `_atomic_write` must call `fcntl.flock`
        with `LOCK_EX` before the write loop and `LOCK_UN` after.

        This is the primary regression test for r7-bughunter-final's
        finding. It is a structural / white-box test on purpose:
        behavioral concurrent-writer tests are unreliable as mutation
        tests because the underlying POSIX guarantee ("atomic only up
        to PIPE_BUF") is a *lower bound* — modern macOS and Linux
        kernels often accept much larger single `os.write` calls
        atomically in practice. A test that spawns processes and
        relies on the race to fire would silently stop catching the
        bug as soon as it runs on a kernel whose short-write behavior
        does not happen to surface the interleave.

        Instead, pin the invariant at the source: the lock MUST be
        acquired exclusive before any write iteration, and MUST be
        released after the last iteration. A spy on `fcntl.flock`
        verifies both, and the ordering spy verifies the lock is held
        across the entire write window (not acquired/released between
        iterations). If a future refactor removes either call, or
        narrows the lock scope, this test fails immediately.
        """
        import fcntl as _fcntl
        import shared.session_journal as sj

        events_seen: list[tuple[str, int]] = []
        real_flock = _fcntl.flock
        real_write = os.write

        def spy_flock(fd: int, op: int) -> None:
            if op == _fcntl.LOCK_EX:
                events_seen.append(("lock_ex", fd))
            elif op == _fcntl.LOCK_UN:
                events_seen.append(("unlock", fd))
            else:
                events_seen.append(("lock_other", op))
            return real_flock(fd, op)

        def spy_write(fd: int, data):
            events_seen.append(("write", fd))
            return real_write(fd, data)

        monkeypatch.setattr(sj.fcntl, "flock", spy_flock)
        monkeypatch.setattr(sj.os, "write", spy_write)

        path = tmp_path / "spied-journal.jsonl"
        data = b'{"v":1,"type":"test","ts":"2026-04-06T00:00:00Z"}\n'

        result = sj._atomic_write(path, data)
        assert result is True
        assert path.read_bytes() == data

        # Structural invariants:
        #   1. Exactly one LOCK_EX before any write(s)
        #   2. At least one write
        #   3. Exactly one LOCK_UN after the last write
        #   4. LOCK_EX precedes every write, LOCK_UN follows every write
        ex_indices = [i for i, e in enumerate(events_seen) if e[0] == "lock_ex"]
        un_indices = [i for i, e in enumerate(events_seen) if e[0] == "unlock"]
        write_indices = [i for i, e in enumerate(events_seen) if e[0] == "write"]

        assert len(ex_indices) == 1, (
            f"expected exactly one LOCK_EX, got {len(ex_indices)}: {events_seen}"
        )
        assert len(un_indices) == 1, (
            f"expected exactly one LOCK_UN, got {len(un_indices)}: {events_seen}"
        )
        assert len(write_indices) >= 1, (
            f"expected at least one os.write call: {events_seen}"
        )
        assert ex_indices[0] < min(write_indices), (
            f"LOCK_EX must precede all writes: {events_seen}"
        )
        assert un_indices[0] > max(write_indices), (
            f"LOCK_UN must follow all writes: {events_seen}"
        )
        # Lock is held across the entire write window, not
        # acquired/released between iterations.
        assert ex_indices[0] < un_indices[0], (
            f"LOCK_EX must precede LOCK_UN: {events_seen}"
        )

    def test_atomic_write_releases_lock_on_write_failure(
        self, tmp_path, monkeypatch
    ):
        """A mid-loop `os.write` failure must still release the lock.

        Companion mutation test: if a refactor drops the `try/finally`
        wrapper around the lock/unlock pair, a failing write would
        leave the lock held until the fd is closed — which works by
        accident today but silently regresses if any future change
        reuses the fd or the release semantics change. Pin the
        explicit unlock by forcing a write failure and asserting both
        the return value and the unlock event.
        """
        import fcntl as _fcntl
        import shared.session_journal as sj

        events_seen: list[str] = []
        real_flock = _fcntl.flock

        def spy_flock(fd: int, op: int) -> None:
            if op == _fcntl.LOCK_EX:
                events_seen.append("lock_ex")
            elif op == _fcntl.LOCK_UN:
                events_seen.append("unlock")
            return real_flock(fd, op)

        def failing_write(fd: int, data) -> int:
            events_seen.append("write_fail")
            return 0  # non-progressing, triggers `n <= 0` branch

        monkeypatch.setattr(sj.fcntl, "flock", spy_flock)
        monkeypatch.setattr(sj.os, "write", failing_write)

        path = tmp_path / "fail-journal.jsonl"
        result = sj._atomic_write(path, b"some payload\n")

        assert result is False, "non-progressing write must return False"
        # Lock acquired, write attempted, lock released — in that order.
        assert events_seen == ["lock_ex", "write_fail", "unlock"], (
            f"expected lock_ex → write_fail → unlock, got {events_seen}"
        )

    def test_concurrent_writers_with_large_events_no_interleaving(self, tmp_path):
        """P1 (behavioral): with flock in place, N processes writing
        M large events each must produce valid JSONL with every event
        accounted for.

        Caveat: this is a *behavioral* corroboration of the structural
        flock tests above, not a mutation test. Modern macOS and Linux
        kernels often accept large (hundreds of KB) single `os.write`
        calls atomically for regular files under moderate load, so
        removing flock may not cause this test to fail on every
        kernel. The primary mutation tests are the two
        `test_atomic_write_holds_*` tests above; this one catches
        behavioral regressions (e.g., writes silently dropped, file
        corruption, wrong line count) under realistic concurrent
        load.

        Invariants asserted:
          1. The total line count equals `num_workers * events_per_worker`.
          2. Every line parses as valid JSON.
          3. Every (worker, seq) tuple appears exactly once.
        """
        num_workers = 8
        events_per_worker = 20
        payload_size = 8 * 1024  # 8 KiB — realistic agent_handoff size

        journal_path = tmp_path / "session-journal.jsonl"
        hooks_path = str(Path(__file__).parent.parent / "hooks")

        # Use "spawn" so the test behaves identically on macOS and
        # Linux and forces fresh interpreters per worker.
        ctx = multiprocessing.get_context("spawn")
        procs = [
            ctx.Process(
                target=_concurrent_large_writer_worker,
                args=(
                    worker_id,
                    events_per_worker,
                    payload_size,
                    str(journal_path),
                    hooks_path,
                ),
            )
            for worker_id in range(num_workers)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)
            assert p.exitcode == 0, (
                f"worker pid={p.pid} exitcode={p.exitcode}"
            )

        # Invariant 1: correct line count (no lost events).
        raw_lines = journal_path.read_text(encoding="utf-8").splitlines()
        expected = num_workers * events_per_worker
        assert len(raw_lines) == expected, (
            f"expected {expected} lines, got {len(raw_lines)}"
        )

        # Invariant 2: every line parses as valid JSON.
        malformed: list[str] = []
        parsed: list[dict] = []
        for line in raw_lines:
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                malformed.append(line[:80])
        assert not malformed, (
            f"{len(malformed)} malformed line(s); first few: {malformed[:3]}"
        )

        # Invariant 3: every (worker, seq) pair appears exactly once.
        seen = {(e["worker"], e["seq"]) for e in parsed}
        expected_pairs = {
            (w, s) for w in range(num_workers) for s in range(events_per_worker)
        }
        assert seen == expected_pairs, (
            f"missing: {expected_pairs - seen}; extra: {seen - expected_pairs}"
        )


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
# Integration: simulated-compaction full lifecycle
# ---------------------------------------------------------------------------


class TestJournalLifecycleSimulatedCompaction:
    """End-to-end lifecycle test for the session journal feature.

    Addresses the end-to-end validation gap noted in PR #350 merge-readiness
    review: every other test in this suite mocks either the write side
    (`sj._get_session_dir`) or the read side (by calling
    `_build_journal_resume(session_dir)` against a path seeded by hand). This
    class asserts the full chain survives an in-memory state reset:

        write_context -> pact_context.get_session_dir -> append_event
        -> [clear in-memory caches to simulate compaction]
        -> read from disk via _build_journal_resume

    What this test does NOT validate (be explicit about the limitations):

    1. **Real platform compaction** — Claude's compaction summarizes the
       model context window; it does not clear Python module state. This
       test substitutes "clear `_cache` / `_context_path` / reimport" for
       "model context was summarized" because the two share the same
       observable precondition for the journal: the in-memory state from
       the prior session is gone, and the only surviving artifact is the
       JSONL file on disk. It catches write/read contract drift; it does
       not catch bugs that require a real compaction event.
    2. **Hook behavior during recovery** — session_init's on-resume logic
       (reconstructing CLAUDE.md, re-invoking resolvers, spawning the
       secretary) is not exercised. See issue #364 for that scope.
    3. **Cross-session recovery** — this test writes and reads under a
       single session_id. A genuine "resume from previous session" flow
       would write events under session_id=A and read them while a new
       session_id=B is active. The journal file path derivation is
       session-scoped, so the cross-session case adds path-resolution
       concerns the single-session lifecycle does not.
    4. **Performance under large journals** — the test writes a handful
       of events. Scalability of `_build_journal_resume` against thousands
       of events is not measured here.

    The test is a coverage *improvement*, not a *complete validation*. Its
    value is catching write/read schema drift end-to-end — the class of
    bug where a writer and a reader agree in isolation (unit tests pass)
    but disagree on the on-disk representation.
    """

    def test_journal_state_survives_simulated_compaction(
        self, tmp_path, monkeypatch
    ):
        """P1: write → clear in-memory state → re-read recovers full journal.

        Uses REAL filesystem persistence via monkeypatched Path.home(), the
        public write API (`append_event` / `make_event`), and the public
        read path (`_build_journal_resume(session_dir)` — the same entry
        point session_init calls on resume). No mocking of the journal
        write or read code paths.

        Determinism: every `make_event(...)` call passes an explicit `ts`
        (monotonically increasing) so the test does not depend on wall
        clock resolution or ordering of same-second events. The
        `_build_journal_resume` active-phase logic uses `max(ts)` to pick
        the latest started phase, so the injected timestamps double as a
        verification that the tie-breaking contract is stable.
        """
        import shared.pact_context as pc
        import shared.session_journal as sj

        # --- Setup: isolate filesystem to tmp_path, reset module caches.
        # This is equivalent to "the previous session wrote these events,
        # then the process ended; now we are in a new process whose
        # in-memory state is empty but the on-disk journal remains."
        monkeypatch.setattr(pc, "_context_path", None)
        monkeypatch.setattr(pc, "_cache", None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Disable the autouse `mock_get_session_dir` fixture so the real
        # pact_context.get_session_dir() drives path derivation — without
        # this line, the autouse fixture pins _get_session_dir to a
        # hard-coded string and bypasses the write_context cache path
        # this test is trying to exercise.
        monkeypatch.setattr(sj, "_get_session_dir", pc.get_session_dir)

        session_id = "11111111-2222-3333-4444-555555555555"
        project_dir = str(tmp_path / "test_project")
        Path(project_dir).mkdir(parents=True)

        # --- Act (phase 1): bootstrap session context and write events.
        pc.write_context("pact-lifecycle", session_id, project_dir, "/plugin/root")

        # Sanity check: the context cache MUST be populated by write_context
        # (otherwise append_event's path derivation will silently fall
        # through and we would be testing a fail-open code path instead
        # of the lifecycle).
        assert pc._cache is not None
        assert pc.get_session_dir() != ""

        from shared.session_journal import append_event, make_event
        from shared.session_resume import _build_journal_resume

        # Explicit, monotonically increasing timestamps — the
        # `_build_journal_resume` active-phase selector sorts by `ts`,
        # so deterministic stamps prove the selector works on the
        # persisted bytes, not on iteration order.
        events = [
            make_event(
                "session_start",
                team="pact-lifecycle",
                session_id=session_id,
                project_dir=project_dir,
                ts="2026-04-06T10:00:00Z",
            ),
            make_event(
                "phase_transition",
                phase="PREPARE",
                status="started",
                ts="2026-04-06T10:01:00Z",
            ),
            make_event(
                "phase_transition",
                phase="PREPARE",
                status="completed",
                ts="2026-04-06T10:02:00Z",
            ),
            make_event(
                "phase_transition",
                phase="CODE",
                status="started",
                ts="2026-04-06T10:03:00Z",
            ),
            make_event(
                "agent_handoff",
                agent="lifecycle-coder",
                task_id="61",
                task_subject="CODE: lifecycle integration test",
                handoff={"decisions": ["Wrote the lifecycle test end-to-end"]},
                ts="2026-04-06T10:04:00Z",
            ),
        ]
        for e in events:
            assert append_event(e) is True, f"append_event failed for {e['type']}"

        # Verify the journal file actually exists on disk before we
        # clear state — if this fails, the rest of the test is moot.
        expected_session_dir = (
            tmp_path / ".claude" / "pact-sessions" / "test_project" / session_id
        )
        expected_journal = expected_session_dir / "session-journal.jsonl"
        assert expected_journal.exists()
        on_disk_lines = expected_journal.read_text(encoding="utf-8").splitlines()
        assert len(on_disk_lines) == len(events)

        # --- Simulate compaction: drop ALL in-memory state that could
        # short-circuit a re-read. This is the closest analog to a
        # context-loss event available from inside a Python test —
        # the journal file on disk is now the ONLY record of the
        # session's prior work.
        pc._cache = None
        pc._context_path = None

        # --- Act (phase 2): reconstruct session_dir from known inputs
        # (slug + session_id) and ask session_resume to rebuild the
        # resume summary from disk. We deliberately pass the path
        # explicitly — _build_journal_resume is the public entry point
        # session_init calls with a previous-session dir, so this
        # mirrors the real recovery code path.
        session_dir_str = str(expected_session_dir)
        resume = _build_journal_resume(session_dir_str)

        # --- Assert: the resume string reflects every persisted fact.
        assert resume is not None, (
            "resume build returned None after simulated compaction — "
            "the on-disk journal was not recovered"
        )
        # Handoff survived
        assert "## Completed Work" in resume
        assert "lifecycle-coder" in resume
        assert "CODE: lifecycle integration test" in resume
        assert "Wrote the lifecycle test end-to-end" in resume
        # Phase progress survived
        assert "Completed phases: PREPARE" in resume
        # CODE was started but never completed — must be the active phase
        # picked by max(ts), not the earlier PREPARE start.
        assert "Last active phase: CODE" in resume
        # PREPARE must NOT appear as the active phase — its latest
        # transition was `completed`, so the started→completed transition
        # must correctly demote it out of the active-phase set.
        assert "Last active phase: PREPARE" not in resume


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
        from unittest.mock import MagicMock

        import shared.session_resume as session_resume
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

        # Spy on datetime.fromisoformat so we can EXPLICITLY assert the catch
        # path was exercised. Without this, the test only proves the function
        # *eventually* falls through to the PR check -- it does not prove the
        # `(ValueError, TypeError, OverflowError)` clause was the route taken.
        # We can't patch attrs on the immutable datetime C type directly, so
        # we replace the module-level `datetime` symbol in session_resume with
        # a MagicMock whose `fromisoformat` raises ValueError. The catch block
        # short-circuits before any other datetime usage in the same `try`,
        # so the mock only needs to provide `fromisoformat`.
        mock_dt = MagicMock()
        mock_dt.fromisoformat.side_effect = ValueError("simulated parse failure")
        with patch.object(session_resume, "datetime", mock_dt), patch(
            "shared.session_resume._check_pr_state", return_value="OPEN"
        ):
            result = _check_journal_paused_state(session_dir)

        # Explicit catch verification: fromisoformat must have been called
        # exactly once (with the corrupt ts; .replace("Z", "+00:00") is a
        # no-op here because the string contains no "Z"), and the function
        # must still have returned the post-catch fall-through message.
        assert mock_dt.fromisoformat.call_count == 1
        assert mock_dt.fromisoformat.call_args.args[0] == "not-a-timestamp"
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
# AdvF2 Approach 4: implicit-API stderr warnings on missing pact_context.init()
# ---------------------------------------------------------------------------


class TestImplicitApiUninitWarnings:
    """The implicit API (append_event/read_events/read_last_event) prints a
    stderr warning -- without changing return values -- when called before
    pact_context.init() AND the journal path cannot be derived. The warning
    only fires on the path-unavailable fail-open branch, so it doesn't add
    noise to in-process tests that monkeypatch _get_session_dir to a real
    path.

    Each test overrides the autouse mock_get_session_dir fixture by
    re-monkeypatching _get_session_dir to return "" inside the test body.
    """

    def test_append_event_warns_when_uninit(self, monkeypatch, capsys):
        import shared.session_journal as sj
        from shared.session_journal import append_event, make_event

        # Override autouse fixture: simulate "no session dir" so _journal_path
        # returns None and we land on the fail-open branch.
        monkeypatch.setattr(sj, "_get_session_dir", lambda: "")
        monkeypatch.setattr(sj, "_pact_context_is_initialized", lambda: False)

        result = append_event(
            make_event(
                "session_start",
                session_id="s1",
                project_dir="/tmp/p",
            ),
        )

        # Fail-open semantics preserved: returns False, no exception.
        assert result is False
        captured = capsys.readouterr()
        assert "append_event called before pact_context.init()" in captured.err
        assert "returning False" in captured.err

    def test_read_events_warns_when_uninit(self, monkeypatch, capsys):
        import shared.session_journal as sj
        from shared.session_journal import read_events

        monkeypatch.setattr(sj, "_get_session_dir", lambda: "")
        monkeypatch.setattr(sj, "_pact_context_is_initialized", lambda: False)

        result = read_events()

        # Fail-open semantics preserved: empty list, no exception.
        assert result == []
        captured = capsys.readouterr()
        assert "read_events called before pact_context.init()" in captured.err
        assert "returning []" in captured.err

    def test_read_last_event_warns_when_uninit(self, monkeypatch, capsys):
        import shared.session_journal as sj
        from shared.session_journal import read_last_event

        monkeypatch.setattr(sj, "_get_session_dir", lambda: "")
        monkeypatch.setattr(sj, "_pact_context_is_initialized", lambda: False)

        result = read_last_event("agent_handoff")

        # Fail-open semantics preserved: None, no exception.
        assert result is None
        captured = capsys.readouterr()
        assert "read_last_event called before pact_context.init()" in captured.err
        assert "returning None" in captured.err

    def test_no_warning_when_path_resolves(self, capsys):
        """When _get_session_dir returns a real path (autouse fixture), the
        functions must NOT emit the warning even if pact_context._context_path
        is technically None -- the test suite has thousands of in-process
        callers that monkeypatch the path resolver instead of init()ing
        pact_context, and Approach 4's warning is intentionally scoped to
        the actual fail-open branch to avoid noise."""
        from shared.session_journal import read_events

        # The autouse mock_get_session_dir fixture is in effect: path resolves
        # to a (non-existent on disk, but non-empty) session dir. read_events
        # should return [] (file doesn't exist) without emitting any warning.
        result = read_events()
        assert result == []
        captured = capsys.readouterr()
        assert "called before pact_context.init()" not in captured.err

    def test_no_warning_when_init_present_but_path_empty(
        self, monkeypatch, capsys
    ):
        """If pact_context IS initialized but the path is still unavailable
        (different failure mode -- e.g. session_id missing from input_data),
        the warning must NOT fire. The init-missing warning is scoped to its
        specific root cause; other path-unavailable failures stay silent to
        preserve the existing fail-open contract."""
        import shared.session_journal as sj
        from shared.session_journal import read_events

        monkeypatch.setattr(sj, "_get_session_dir", lambda: "")
        monkeypatch.setattr(sj, "_pact_context_is_initialized", lambda: True)

        result = read_events()
        assert result == []
        captured = capsys.readouterr()
        assert "called before pact_context.init()" not in captured.err


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
# Integration: agent_handoff_emitter journal write (#538 replacement for
# prior handoff_gate coverage; handoff_gate removed in C2a)
# ---------------------------------------------------------------------------


class TestAgentHandoffEmitterJournalWrite:
    """Tests that agent_handoff_emitter.main() writes agent_handoff event to journal."""

    def _run_main(self, input_data, task_data, tmp_path, monkeypatch):
        """Helper to run agent_handoff_emitter.main() with given stdin and task file."""
        import io
        from unittest.mock import patch as _patch

        monkeypatch.setenv("HOME", str(tmp_path))
        stdin = io.StringIO(json.dumps(input_data))

        with _patch("sys.stdin", stdin), \
             _patch("agent_handoff_emitter.read_task_json", return_value=task_data), \
             _patch("agent_handoff_emitter.pact_context"), \
             _patch("agent_handoff_emitter.get_team_name", return_value="pact-test1234"), \
             _patch("agent_handoff_emitter.append_event", return_value=True) as mock_append, \
             _patch("agent_handoff_emitter.make_event", wraps=None) as mock_make:

            mock_make.return_value = {"v": 1, "type": "agent_handoff", "ts": "2026-01-01T00:00:00Z"}

            with pytest.raises(SystemExit) as exc:
                from agent_handoff_emitter import main
                main()

            return exc.value.code, mock_append, mock_make

    def test_writes_journal_on_completed_task(self, tmp_path, monkeypatch):
        """Writes agent_handoff event when status=completed + owner + non-signal."""
        input_data = {
            "task_id": "7",
            "task_subject": "CODE: auth",
            "team_name": "pact-test1234",
        }
        task_data = {
            "status": "completed",
            "owner": "backend-coder",
            "metadata": {
                "handoff": {
                    "produced": ["src/auth.ts"],
                    "decisions": ["Used JWT"],
                    "uncertainty": [],
                    "integration": ["UserService"],
                    "open_questions": [],
                },
            },
        }

        exit_code, mock_append, _ = self._run_main(input_data, task_data, tmp_path, monkeypatch)

        assert exit_code == 0
        mock_append.assert_called_once()

    def test_no_journal_write_on_in_progress_taskupdate(self, tmp_path, monkeypatch):
        """Does NOT write journal event on metadata-only TaskUpdate (#528 guard)."""
        input_data = {
            "task_id": "7",
            "task_subject": "CODE: auth",
            "team_name": "pact-test1234",
        }
        task_data = {
            "status": "in_progress",
            "owner": "backend-coder",
            "metadata": {"briefing_delivered": True},
        }

        exit_code, mock_append, _ = self._run_main(input_data, task_data, tmp_path, monkeypatch)

        assert exit_code == 0
        mock_append.assert_not_called()


# ---------------------------------------------------------------------------
# Integration: session_init journal write
# ---------------------------------------------------------------------------


class TestSessionInitJournalWrite:
    """Tests that session_init.main() writes session_start event to journal."""

    @pytest.mark.parametrize(
        "stdin_source, expected_on_disk",
        [
            ("startup", "startup"),
            ("resume", "resume"),
            ("compact", "compact"),
            ("clear", "clear"),
            ("unknown", "unknown"),
            (42, "unknown"),
        ],
    )
    def test_writes_session_start_event(
        self, journal_home, monkeypatch, stdin_source, expected_on_disk
    ):
        """session_init.main() writes session_start event to journal."""
        # NOTE: The class-level `mock_get_session_dir` autouse fixture at
        # test_session_journal.py:188-197 patches `_get_session_dir()` on the
        # session_journal module, so this test bypasses the real
        # `pact_context`-backed session-dir derivation. What IS verified
        # end-to-end: the source-normalization branches in
        # session_init.main(), journal serialization to JSONL on disk, and
        # read_events() round-trip — including the non-string isinstance
        # guard via the (42, "unknown") case. What is NOT exercised:
        # `pact_context.init()` path resolution.
        import io

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(journal_home / "project"))
        (journal_home / "project").mkdir()
        (journal_home / "project" / "CLAUDE.md").write_text("# Project\n## Retrieved Context\n")

        input_data = {
            "session_id": "abc12345-test",
            "source": stdin_source,
        }

        with patch("sys.stdin", io.StringIO(json.dumps(input_data))), \
             patch("session_init.setup_plugin_symlinks", return_value=None), \
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
        assert events[0].get("source") == expected_on_disk


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

    def test_non_tilde_absolute_path_outside_prefix_is_rejected(self, tmp_path):
        """F: an absolute Session dir path outside ~/.claude/pact-sessions is rejected.

        Contract flip from earlier behavior: previously the function returned
        any absolute path verbatim, including paths outside the canonical
        pact-sessions tree. The F-fix added _validate_under_pact_sessions which
        rejects (returns None for) any path that does not live under
        ~/.claude/pact-sessions. Defense-in-depth against tampered CLAUDE.md
        content that could redirect the function at /etc, /var, or a sibling
        project's secrets.

        See test_session_init.TestExtractPrevSessionDirDualLocation for the
        full F-fix coverage; this test pins the contract from the
        test_session_journal angle as well so a regression in either file
        is caught.
        """
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
        # /var/data/... is NOT under ~/.claude/pact-sessions → validator returns None.
        assert result is None


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

    def test_write_stdin_preserves_apostrophes_in_payload(
        self, journal_home, session_dir, journal_file,
    ):
        """r12-fix: --stdin path preserves apostrophes verbatim (HIGH r9).

        Round-9 review found that command files (orchestrate/peer-review/
        comPACT/pause) built CLI calls of the shape:

            python3 session_journal.py write ... --data '{"message": "..."}'

        When the orchestrator template-substituted a value containing an
        apostrophe (e.g. commit message `fix: don't crash` or branch
        `feat/o'connor-fix`), the apostrophe closed the bash single-quoted
        --data argument prematurely. With `set -e` + ERR trap the call
        failed silently and the journal event was dropped — a workflow
        state-loss bug.

        The fix introduces a `--stdin` flag so call sites can pipe JSON via
        a quoted heredoc (`<<'JSON' ... JSON`), which is immune to shell
        quoting. This test pins that pinky-promise: when JSON containing
        apostrophes is fed via stdin, the event lands on disk with every
        apostrophe preserved character-for-character.
        """
        # Mirror the exact shape of a `commit` event the orchestrator builds.
        payload = {
            "sha": "abc1234",
            "message": "fix: don't crash on o'connor's edge case",
            "phase": "CODE",
            "branch": "feat/o'connor-fix",
        }
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--type", "commit",
                "--session-dir", session_dir,
                "--stdin",
            ],
            input=json.dumps(payload),
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 0, result.stderr

        # Verify the event was written and apostrophes round-tripped intact.
        assert journal_file.exists()
        event = json.loads(journal_file.read_text().strip())
        assert event["type"] == "commit"
        assert event["sha"] == "abc1234"
        assert event["message"] == "fix: don't crash on o'connor's edge case"
        assert event["branch"] == "feat/o'connor-fix"
        # Sanity: every apostrophe survived (3 in message, 1 in branch).
        assert event["message"].count("'") == 3
        assert event["branch"].count("'") == 1

    def test_write_stdin_and_data_are_mutually_exclusive(
        self, journal_home, session_dir, journal_file,
    ):
        """r12-fix: --stdin and --data cannot be combined.

        The argparse mutually-exclusive group should reject any invocation
        that supplies both flags, since the resulting precedence would be
        ambiguous. argparse rejects mutex violations with exit code 2.
        """
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--type", "test_event",
                "--session-dir", session_dir,
                "--data", '{"k": "v"}',
                "--stdin",
            ],
            input='{"k": "v"}',
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        # argparse rejects mutex violations with exit 2 and "not allowed with"
        # in stderr. Pin both so a future argparse upgrade that changes the
        # exit code surfaces here instead of in production.
        assert result.returncode == 2
        assert "not allowed with" in result.stderr
        # Journal must not have been written.
        assert not journal_file.exists() or journal_file.read_text() == ""

    def test_write_stdin_invalid_json_reports_stdin_source(
        self, journal_home, session_dir, journal_file,
    ):
        """r12-fix: --stdin error message names `stdin`, not `--data`.

        Operators reading the error must be able to tell which input channel
        produced the malformed JSON. The CLI branches the error message on
        the data source so a stdin-pipe failure surfaces as
        `invalid stdin JSON`, not the legacy `invalid --data JSON` (which
        would mislead during debugging).
        """
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--type", "test_event",
                "--session-dir", session_dir,
                "--stdin",
            ],
            input="not-json",
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 1
        assert "invalid stdin JSON" in result.stderr
        # And it must NOT mention --data, since that wasn't the source.
        assert "--data" not in result.stderr
        # Journal must not have been written.
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
        "cleanup_summary": {},  # No required fields; optional-only (#412 Fix B).
        "session_consolidated": {},  # No required fields; optional-only (#453 Fix B).
        # `wake_tally_warn` is emitted by
        # `shared.wake_lifecycle._warn_empty_team_config_once` when the
        # step-4 owner-classification falls through fail-CONSERVATIVE
        # (empty members list). `team_name` identifies which team's
        # config is unreadable; `reason` is a categorical token so a
        # future log-filter can dispatch on the failure mode. The free-
        # form `detail` field that the production call site also passes
        # is documentation-grade prose and is intentionally NOT required.
        "wake_tally_warn": {
            "team_name": "team-warn-sample",
            "reason": "empty_team_config_fail_conservative",
        },
    }

    def test_samples_mirror_required_fields_dict(self):
        """Meta-test: _SAMPLES must cover every key in _REQUIRED_FIELDS_BY_TYPE.

        If this test fails, a new event type was added to the source dict
        without adding a matching sample here — guards against the per-type
        check silently skipping coverage of new types.

        Also verifies each sample value has the expected Python type from
        the source dict — otherwise happy-path tests would accidentally
        exercise the validator with mis-typed samples.
        """
        from shared.session_journal import _REQUIRED_FIELDS_BY_TYPE

        assert set(self._SAMPLES.keys()) == set(_REQUIRED_FIELDS_BY_TYPE.keys())
        for event_type, field_types in _REQUIRED_FIELDS_BY_TYPE.items():
            sample = self._SAMPLES[event_type]
            for field, expected_type in field_types.items():
                assert field in sample, (
                    f"sample for {event_type} missing required field {field!r}"
                )
                value = sample[field]
                # Mirror the validator's bool-in-int rejection.
                assert not (expected_type is int and isinstance(value, bool)), (
                    f"sample for {event_type}.{field} is bool but schema says int"
                )
                assert isinstance(value, expected_type), (
                    f"sample for {event_type}.{field} is "
                    f"{type(value).__name__}, expected {expected_type.__name__}"
                )

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

    # -- Fix A (RA1): per-field type checks -------------------------------

    # One wrong-typed sample per required field for each event type. Each
    # tuple is (event_type, field, bad_value, expected_type_name,
    # got_type_name). Built from _REQUIRED_FIELDS_BY_TYPE so adding a new
    # schema entry forces a matching mismatch sample.
    _TYPE_MISMATCHES: list = [
        # str fields fed an int
        ("phase_transition", "phase", 42, "str", "int"),
        ("phase_transition", "status", 42, "str", "int"),
        ("checkpoint", "phase", 42, "str", "int"),
        ("agent_dispatch", "agent", 42, "str", "int"),
        ("agent_handoff", "task_subject", 42, "str", "int"),
        ("commit", "sha", 42, "str", "int"),
        ("review_dispatch", "pr_url", 42, "str", "int"),
        ("review_finding", "severity", 42, "str", "int"),
        ("remediation", "fixer", 42, "str", "int"),
        ("session_paused", "branch", 42, "str", "int"),
        ("session_start", "session_id", 42, "str", "int"),
        ("variety_assessed", "task_id", 42, "str", "int"),
        # int fields fed a str
        ("review_dispatch", "pr_number", "42", "int", "str"),
        ("remediation", "cycle", "1", "int", "str"),
        ("pr_ready", "commits", "7", "int", "str"),
        ("session_paused", "pr_number", "42", "int", "str"),
        # dict fields fed a list
        ("variety_assessed", "variety", [1, 2], "dict", "list"),
        ("agent_handoff", "handoff", [1, 2], "dict", "list"),
        ("s2_state_seeded", "boundaries", [1, 2], "dict", "list"),
        # list fields fed a dict
        ("s2_state_seeded", "agents", {"k": "v"}, "list", "dict"),
        ("review_dispatch", "reviewers", {"k": "v"}, "list", "dict"),
        ("remediation", "items", {"k": "v"}, "list", "dict"),
        # bool field fed an int (bool fields currently only consolidation_completed)
        ("session_paused", "consolidation_completed", 1, "bool", "int"),
    ]

    @pytest.mark.parametrize(
        "event_type, field, bad_value, expected_name, got_name",
        _TYPE_MISMATCHES,
        ids=[f"{t}.{f}" for t, f, *_ in _TYPE_MISMATCHES],
    )
    def test_required_field_type_mismatch_rejected(
        self, event_type, field, bad_value, expected_name, got_name,
    ):
        """RA1: Per-field type check rejects wrong Python types.

        The validator's per-type dict now maps each required field to its
        expected Python type. A writer that produces the right field name
        but the wrong type (e.g. `phase=42` instead of `phase="CODE"`)
        must be rejected with a reason that names BOTH the expected type
        and the actual type, so debugging from the stderr line is sharp.
        """
        from shared.session_journal import _validate_event_schema, make_event

        sample = dict(self._SAMPLES[event_type])
        sample[field] = bad_value
        event = make_event(event_type, **sample)
        ok, reason = _validate_event_schema(event)
        assert ok is False, (
            f"{event_type}.{field}={bad_value!r} should be rejected"
        )
        expected_reason = (
            f"field '{field}' for type '{event_type}' must be "
            f"{expected_name}, got {got_name}"
        )
        assert reason == expected_reason, (
            f"expected {expected_reason!r}, got {reason!r}"
        )

    def test_int_field_rejects_bool_explicitly(self):
        """RA1: int field rejects bool even though bool subclasses int.

        Symmetric with the baseline `v must be int` rejection of True/False.
        Without this guard, a writer passing `pr_number=True` for a
        `review_dispatch` event would slip through the isinstance(int)
        check because `isinstance(True, int)` returns True in Python.
        """
        from shared.session_journal import _validate_event_schema, make_event

        sample = dict(self._SAMPLES["review_dispatch"])
        sample["pr_number"] = True
        event = make_event("review_dispatch", **sample)
        ok, reason = _validate_event_schema(event)
        assert ok is False
        assert reason == (
            "field 'pr_number' for type 'review_dispatch' must be int, "
            "got bool"
        )

    # -- Fix B (RG2): empty/whitespace-only strings rejected --------------

    @pytest.mark.parametrize(
        "event_type, field, bad_value",
        [
            ("phase_transition", "phase", ""),
            ("phase_transition", "phase", "   "),
            ("phase_transition", "phase", "\t"),
            ("phase_transition", "status", ""),
            ("agent_handoff", "task_id", ""),
            ("agent_handoff", "agent", "  \n  "),
            ("commit", "sha", ""),
            ("commit", "message", "\t\t"),
            ("session_start", "session_id", ""),
            ("session_start", "project_dir", "   "),
        ],
        ids=[
            "phase_empty",
            "phase_spaces",
            "phase_tab",
            "status_empty",
            "handoff_task_id_empty",
            "handoff_agent_whitespace",
            "commit_sha_empty",
            "commit_message_tabs",
            "session_id_empty",
            "project_dir_spaces",
        ],
    )
    def test_required_str_field_rejects_empty_and_whitespace(
        self, event_type, field, bad_value,
    ):
        """RG2: str fields additionally reject empty / whitespace-only.

        A blank `phase` or `agent` or `task_id` passes the isinstance(str)
        check but is functionally indistinguishable from missing for every
        downstream consumer. The validator strips before checking, so
        "", " ", "\\t", and "   \\n  " all surface the same reason
        ("must be non-empty string") — consistent with the baseline
        `type must be non-empty str` behavior.
        """
        from shared.session_journal import _validate_event_schema, make_event

        sample = dict(self._SAMPLES[event_type])
        sample[field] = bad_value
        event = make_event(event_type, **sample)
        ok, reason = _validate_event_schema(event)
        assert ok is False, (
            f"{event_type}.{field}={bad_value!r} should be rejected"
        )
        expected = (
            f"field '{field}' for type '{event_type}' must be "
            f"non-empty string"
        )
        assert reason == expected, f"expected {expected!r}, got {reason!r}"

    def test_cli_write_rejects_type_mismatch(
        self, journal_home, session_dir, journal_file,
    ):
        """CLI write path surfaces the type-mismatch reason on stderr.

        Dual-API check for RA1: the CLI subcommand (used by orchestrator
        command bash blocks) must reject wrong-typed fields with the same
        precise reason as the in-process API. Uses `phase_transition`
        with `phase=42` (a number instead of a string) as the canary —
        the same field that BugF1 involved.
        """
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--type", "phase_transition",
                "--session-dir", session_dir,
                "--data", '{"phase": 42, "status": "started"}',
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 1
        assert "invalid event schema" in result.stderr
        assert (
            "field 'phase' for type 'phase_transition' must be str, got int"
            in result.stderr
        )
        assert not journal_file.exists() or journal_file.read_text() == ""

    def test_cli_write_rejects_empty_string_required_field(
        self, journal_home, session_dir, journal_file,
    ):
        """CLI write path surfaces the empty-string reason on stderr (RG2).

        Dual-API check for RG2: the CLI subcommand must reject empty
        `phase` with the "non-empty string" reason, not the isinstance
        "must be str" reason. This pins the reason string so operators
        get a sharper diagnostic than a generic "invalid event schema".
        """
        result = subprocess.run(
            [
                sys.executable, _SJ_SCRIPT, "write",
                "--type", "phase_transition",
                "--session-dir", session_dir,
                "--data", '{"phase": "", "status": "started"}',
            ],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(journal_home)},
        )
        assert result.returncode == 1
        assert "invalid event schema" in result.stderr
        assert (
            "field 'phase' for type 'phase_transition' must be "
            "non-empty string"
        ) in result.stderr
        assert not journal_file.exists() or journal_file.read_text() == ""


# ---------------------------------------------------------------------------
# Per-type optional-field validation (BR-M2)
# ---------------------------------------------------------------------------


class TestValidateOptionalFieldTypes:
    """Per-type optional-field schema validation tests for BR-M2.

    The validator enforces type on optional fields declared in
    _OPTIONAL_FIELDS_BY_TYPE. A field is optional iff absent is OK; when
    present, it must match the declared Python type. This is the schema
    contract counterpart to runtime clamps (e.g. the `source` isinstance
    guard in session_init.py) — a future writer that bypasses the clamp
    and emits the wrong type directly to `make_event` is rejected at
    validate time instead of landing a bad type on disk.
    """

    def test_optional_fields_dict_shape(self):
        """_OPTIONAL_FIELDS_BY_TYPE maps event_type → {field: type}.

        Meta-test: validates the declaration shape so a malformed entry
        fails here rather than surfacing as a cryptic TypeError inside
        the validator.
        """
        from shared.session_journal import _OPTIONAL_FIELDS_BY_TYPE

        assert isinstance(_OPTIONAL_FIELDS_BY_TYPE, dict)
        for event_type, field_types in _OPTIONAL_FIELDS_BY_TYPE.items():
            assert isinstance(event_type, str) and event_type.strip()
            assert isinstance(field_types, dict)
            for field, expected_type in field_types.items():
                assert isinstance(field, str) and field.strip()
                assert isinstance(expected_type, type)

    def test_session_start_source_declared_optional(self):
        """session_start has `source: str` in _OPTIONAL_FIELDS_BY_TYPE.

        Pins the canonical case — the isinstance guard in session_init.py
        clamps `source` at runtime; this schema entry pins the contract
        at the journal boundary.
        """
        from shared.session_journal import _OPTIONAL_FIELDS_BY_TYPE

        assert _OPTIONAL_FIELDS_BY_TYPE.get("session_start") == {"source": str}

    def test_optional_field_correct_type_passes(self):
        """Optional field with correct type passes validation."""
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event(
            "session_start",
            session_id="s1",
            project_dir="/tmp/p",
            source="startup",
        )
        ok, reason = _validate_event_schema(event)
        assert ok is True
        assert reason == "ok"

    def test_optional_field_absent_passes(self):
        """Optional field missing entirely passes validation.

        That's what "optional" means — absence is not a violation. All
        existing session_init writers prior to R2 took this code path.
        """
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event("session_start", session_id="s1", project_dir="/tmp/p")
        ok, reason = _validate_event_schema(event)
        assert ok is True
        assert reason == "ok"

    def test_optional_field_none_passes(self):
        """Optional field explicitly set to None passes validation.

        Symmetric with the required-field check (`field not in event or
        event[field] is None`): for optional fields, None is treated the
        same as missing — both skip the type check.
        """
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event(
            "session_start",
            session_id="s1",
            project_dir="/tmp/p",
            source=None,
        )
        ok, reason = _validate_event_schema(event)
        assert ok is True
        assert reason == "ok"

    @pytest.mark.parametrize(
        "bad_value, got_name",
        [
            (42, "int"),
            (3.14, "float"),
            ([1, 2], "list"),
            ({"k": "v"}, "dict"),
            ((1, 2), "tuple"),
            (b"bytes", "bytes"),
        ],
        ids=["int", "float", "list", "dict", "tuple", "bytes"],
    )
    def test_optional_field_wrong_type_rejected(self, bad_value, got_name):
        """Optional field with wrong type fails validation with precise reason.

        Reason-string format mirrors the required-field mismatch reason
        but uses "optional field" prefix so CLI stderr diagnostics make
        the source of the failure unambiguous. This pins the format so
        operators get a sharp diagnostic instead of a generic 'invalid
        event schema'.
        """
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event(
            "session_start",
            session_id="s1",
            project_dir="/tmp/p",
            source=bad_value,
        )
        ok, reason = _validate_event_schema(event)
        assert ok is False, f"source={bad_value!r} should be rejected"
        expected = (
            f"optional field 'source' for type 'session_start' must "
            f"be str, got {got_name}"
        )
        assert reason == expected, f"expected {expected!r}, got {reason!r}"

    def test_optional_str_field_rejects_empty_and_whitespace(self):
        """Optional str fields reject empty/whitespace-only values.

        Symmetric with required-str semantics — a whitespace-only
        `source` ("" or "   ") is indistinguishable from missing for
        downstream consumers. The validator strips before checking.
        """
        from shared.session_journal import _validate_event_schema, make_event

        for bad in ["", "   ", "\t", "\n", "   \t  "]:
            event = make_event(
                "session_start",
                session_id="s1",
                project_dir="/tmp/p",
                source=bad,
            )
            ok, reason = _validate_event_schema(event)
            assert ok is False, f"source={bad!r} should be rejected"
            assert reason == (
                "optional field 'source' for type 'session_start' must "
                "be non-empty string"
            )

    def test_undeclared_event_type_with_optional_looking_field_passes(self):
        """Event types with no optional declaration don't trip on arbitrary fields.

        `phase_transition` is not in _OPTIONAL_FIELDS_BY_TYPE, so even a
        same-named field with a wrong type on that event passes optional
        validation. This guards against cross-contamination — optional
        declarations are per-event-type, not global.
        """
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event(
            "phase_transition",
            phase="CODE",
            status="started",
            source=42,  # Would fail on session_start; passes on phase_transition.
        )
        ok, reason = _validate_event_schema(event)
        assert ok is True
        assert reason == "ok"

    def test_unknown_event_type_passes_optional_check(self):
        """Unknown event types bypass the optional-field loop.

        Mirrors the required-field "unknown type is opt-in" behavior:
        per-type checks are opt-in whitelists, so free-form "test" types
        used in unit tests sail through regardless of payload shape.
        """
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event(
            "some_unit_test_type_not_in_dict",
            source=42,
        )
        ok, reason = _validate_event_schema(event)
        assert ok is True
        assert reason == "ok"

    def test_append_event_rejects_wrong_typed_optional_field(self, journal_home):
        """append_event returns False when optional field has wrong type.

        End-to-end check: the write path (fail-open) rejects the event
        without ever touching disk. A future session_init regression that
        forgets the isinstance guard would produce a wrong-typed source;
        this test is the schema-level bulwark against that reaching disk.
        """
        from shared.session_journal import append_event, make_event

        bad_event = make_event(
            "session_start",
            session_id="s1",
            project_dir="/tmp/p",
            source=42,
        )
        assert append_event(bad_event) is False

    def test_append_event_accepts_correct_optional_field(self, journal_home):
        """append_event returns True for a well-formed session_start with source.

        The positive end-to-end case — pins the happy path of the R2
        contract so a regression in the validator (e.g. rejecting str
        values accidentally) fails here instead of silently dropping
        every session_start write.
        """
        from shared.session_journal import append_event, make_event, read_events

        good_event = make_event(
            "session_start",
            session_id="s1",
            project_dir="/tmp/p",
            source="startup",
        )
        assert append_event(good_event) is True
        events = read_events("session_start")
        assert len(events) == 1
        assert events[0].get("source") == "startup"

    def test_cleanup_summary_all_fields_pass(self):
        """cleanup_summary happy path — all declared fields with correct types.

        Cycle-8: single `ttl_days` split into `teams_ttl_days` /
        `tasks_ttl_days`; single `reaper_ran` split into `teams_ran` /
        `tasks_ran`.
        """
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event(
            "cleanup_summary",
            teams_reaped=3,
            teams_skipped=1,
            tasks_reaped=2,
            tasks_skipped=0,
            teams_ttl_days=30,
            tasks_ttl_days=30,
            teams_ran=True,
            tasks_ran=True,
        )
        ok, reason = _validate_event_schema(event)
        assert ok is True
        assert reason == "ok"

    def test_cleanup_summary_declared_optional_fields(self):
        """cleanup_summary fields are declared in _OPTIONAL_FIELDS_BY_TYPE.

        Pin the schema contract. Enforcement is ACTIVE: cleanup_summary
        is registered in _REQUIRED_FIELDS_BY_TYPE with {}, which defeats
        the unknown-type short-circuit and activates the optional-field
        loop. Cycle-8 split `ttl_days`/`reaper_ran` into per-reaper
        fields.
        """
        from shared.session_journal import _OPTIONAL_FIELDS_BY_TYPE

        assert _OPTIONAL_FIELDS_BY_TYPE.get("cleanup_summary") == {
            "teams_reaped": int,
            "teams_skipped": int,
            "tasks_reaped": int,
            "tasks_skipped": int,
            "teams_ttl_days": int,
            "tasks_ttl_days": int,
            "teams_ran": bool,
            "tasks_ran": bool,
        }

    def test_validate_rejects_wrong_type_cleanup_summary(self):
        """Wrong-type optional field on cleanup_summary is rejected live.

        Load-bearing for the optional-field activation invariant: this
        test fails if `_REQUIRED_FIELDS_BY_TYPE["cleanup_summary"] = {}`
        is removed, because the validator's unknown-type short-circuit
        would then accept the str value and return (True, "ok"). The
        empty-dict registration IS the activation switch.
        """
        from shared.session_journal import _validate_event_schema, make_event

        bad_str = make_event(
            "cleanup_summary",
            teams_reaped="3",  # wrong type — str, not int
            teams_skipped=0,
            tasks_reaped=0,
            tasks_skipped=0,
            teams_ttl_days=30,
            tasks_ttl_days=30,
        )
        ok, reason = _validate_event_schema(bad_str)
        assert ok is False
        assert "teams_reaped" in reason
        assert "must be int" in reason
        assert "got str" in reason

        # Symmetric: bool rejected as int (parity with required-field checks).
        bad_bool = make_event(
            "cleanup_summary",
            teams_reaped=True,
            teams_skipped=0,
            tasks_reaped=0,
            tasks_skipped=0,
            teams_ttl_days=30,
            tasks_ttl_days=30,
        )
        ok2, reason2 = _validate_event_schema(bad_bool)
        assert ok2 is False
        assert "got bool" in reason2

    @pytest.mark.parametrize("field_name", ["teams_ttl_days", "tasks_ttl_days"])
    @pytest.mark.parametrize("bad_value,expected_got", [
        ("30", "str"),
        (True, "bool"),  # bool-as-int trap — must be rejected
        ([30], "list"),
    ])
    def test_validate_rejects_wrong_type_per_reaper_ttl_days(self, field_name, bad_value, expected_got):
        """Cycle-8 Test 4 — `teams_ttl_days`/`tasks_ttl_days` must each be int.

        COUNTER-TEST BY REVERT target: removing either field from
        `_OPTIONAL_FIELDS_BY_TYPE["cleanup_summary"]` flips the
        parametrization case for that field — the validator's
        optional-field loop silently accepts wrong types for unknown
        fields.

        Parametrization shape mirrors `test_validate_rejects_wrong_type_
        per_reaper_ran` (cycle-8 reaper-ran split): both halves of the
        TTL split get independent coverage so a regression that drops
        one field's schema entry is caught.

        bool is load-bearing: Python `True == 1`, so an int-typed field
        must still reject bool values (else `teams_ttl_days=True` would
        silently pass as "1 day").
        """
        from shared.session_journal import _validate_event_schema, make_event

        kwargs = {
            "teams_reaped": 0,
            "teams_skipped": 0,
            "tasks_reaped": 0,
            "tasks_skipped": 0,
            "teams_ttl_days": 30,
            "tasks_ttl_days": 30,
            field_name: bad_value,  # overrides the good default above
        }
        event = make_event("cleanup_summary", **kwargs)
        ok, reason = _validate_event_schema(event)
        assert ok is False
        assert field_name in reason
        assert "must be int" in reason
        assert f"got {expected_got}" in reason

    @pytest.mark.parametrize("field_name", ["teams_ran", "tasks_ran"])
    @pytest.mark.parametrize("bad_value,expected_got", [
        ("yes", "str"),
        (1, "int"),
        (0, "int"),
    ])
    def test_validate_rejects_wrong_type_per_reaper_ran(self, field_name, bad_value, expected_got):
        """`teams_ran` and `tasks_ran` must each be bool — reject str, int (both 1 and 0).

        Cycle-8 split the single `reaper_ran` bool into per-reaper bools.
        This parametrization covers BOTH halves — a regression that flips
        only one side's declared type (e.g. `teams_ran: int`) would fail
        exactly the affected parametrization cell.

        int is load-bearing: Python bools ARE ints (True == 1), so
        without an explicit bool-vs-int check in the validator,
        `teams_ran=1` would silently pass as "True-ish" and poison
        downstream audit-log consumers who rely on the strict
        True/False discriminator.

        Note: value=None is NOT tested as a reject case because the
        validator's optional-field path explicitly treats None as
        "field absent" (session_journal.py:324) — consistent with the
        `continue` semantics for missing optional fields. This is
        intentional and correct for OPTIONAL fields.
        """
        from shared.session_journal import _validate_event_schema, make_event

        kwargs = {
            "teams_reaped": 0,
            "teams_skipped": 0,
            "tasks_reaped": 0,
            "tasks_skipped": 0,
            "teams_ttl_days": 30,
            "tasks_ttl_days": 30,
            field_name: bad_value,
        }
        event = make_event("cleanup_summary", **kwargs)
        ok, reason = _validate_event_schema(event)
        assert ok is False
        assert field_name in reason
        assert "must be bool" in reason
        assert f"got {expected_got}" in reason

    @pytest.mark.parametrize("field_name", ["teams_ran", "tasks_ran"])
    def test_validate_accepts_per_reaper_ran_happy_path(self, field_name):
        """`teams_ran`=True/False and `tasks_ran`=True/False all pass.

        Happy-path pin for both bool fields (cycle-8). Pins the positive
        side so a future refactor that over-tightened the validator
        (e.g. accepted only True) would fail here.
        """
        from shared.session_journal import _validate_event_schema, make_event

        for value in (True, False):
            kwargs = {
                "teams_reaped": 0,
                "teams_skipped": 0,
                "tasks_reaped": 0,
                "tasks_skipped": 0,
                "teams_ttl_days": 30,
                "tasks_ttl_days": 30,
                field_name: value,
            }
            event = make_event("cleanup_summary", **kwargs)
            ok, reason = _validate_event_schema(event)
            assert ok is True, f"{field_name}={value!r} should pass; got {reason!r}"
            assert reason == "ok"

    def test_validate_rejects_wrong_type_session_end_warning(self):
        """session_end.warning wrong-type is rejected live (#433 cycle-7 N1).

        COUNTER-TEST BY REVERT target: removing
        `"session_end": {"warning": str}` from `_OPTIONAL_FIELDS_BY_TYPE`
        in shared/session_journal.py flips this test to pass-as-OK
        because `_validate_event_schema` has no declared optional
        fields for `session_end` and the int value sails through the
        optional-field loop silently. The active empty-dict entry for
        session_end in `_REQUIRED_FIELDS_BY_TYPE` combined with the
        `{"warning": str}` entry here IS the activation mechanism.

        session_end.py:687-688 writes `make_event("session_end",
        warning=<str>)` when check_unpaused_pr detects an open-but-
        unpaused PR. Without the schema entry, a future writer could
        pass a non-string warning (e.g. a dict of findings) and no
        validator would catch it — downstream audit consumers would
        blow up on unexpected types.
        """
        from shared.session_journal import _validate_event_schema, make_event

        bad = make_event("session_end", warning=42)  # wrong type — int, not str
        ok, reason = _validate_event_schema(bad)
        assert ok is False
        assert "warning" in reason
        assert "must be str" in reason
        assert "got int" in reason

    def test_validate_accepts_session_end_warning_str(self):
        """Happy-path partner: well-formed warning string passes (#433 cycle-7 N1)."""
        from shared.session_journal import _validate_event_schema, make_event

        good = make_event("session_end", warning="open-pr-detected: #433")
        ok, reason = _validate_event_schema(good)
        assert ok is True, f"valid warning should pass; got {reason!r}"
        assert reason == "ok"

    # ---------------------------------------------------------------------
    # session_consolidated schema activation + optional-field tests (#453)
    # ---------------------------------------------------------------------

    def test_session_consolidated_schema_activated(self):
        """session_consolidated registered with {} in _REQUIRED_FIELDS_BY_TYPE.

        Empty-dict registration in the required-fields dict is the
        activation switch for the optional-field enforcement loop.
        Without this entry, `_validate_event_schema` short-circuits on
        "unknown type" before reaching the optional-field checks and
        every wrong-typed payload would silently pass.
        """
        from shared.session_journal import _REQUIRED_FIELDS_BY_TYPE

        assert _REQUIRED_FIELDS_BY_TYPE.get("session_consolidated") == {}

    def test_session_consolidated_declared_optional_fields(self):
        """session_consolidated optional fields pinned in _OPTIONAL_FIELDS_BY_TYPE.

        Pins the three audit-trail fields (pass, task_count, memories_saved)
        so a regression that drops any of them is caught at test time.
        """
        from shared.session_journal import _OPTIONAL_FIELDS_BY_TYPE

        assert _OPTIONAL_FIELDS_BY_TYPE.get("session_consolidated") == {
            "pass": int,
            "task_count": int,
            "memories_saved": int,
        }

    def test_session_consolidated_absent_fields_pass(self):
        """Event with no optional fields validates (all fields optional).

        The event's mere EXISTENCE is the detector signal; payload is
        advisory. This test pins that an empty-payload event is a valid
        write — orchestrators that cannot produce counts must still be
        able to emit the signal.
        """
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event("session_consolidated")
        ok, reason = _validate_event_schema(event)
        assert ok is True, f"empty session_consolidated should pass; got {reason!r}"
        assert reason == "ok"

    @pytest.mark.parametrize("field_name", ["pass", "task_count", "memories_saved"])
    def test_session_consolidated_int_field_happy_path(self, field_name):
        """Each optional int field accepts a plain int value."""
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event("session_consolidated", **{field_name: 2})
        ok, reason = _validate_event_schema(event)
        assert ok is True, f"{field_name}=2 should pass; got {reason!r}"
        assert reason == "ok"

    @pytest.mark.parametrize("field_name", ["pass", "task_count", "memories_saved"])
    def test_session_consolidated_int_field_rejects_bool(self, field_name):
        """bool is rejected as int for each optional field.

        Python bool subclasses int (True == 1), so without the explicit
        bool-in-int guard in the validator a writer could pass `pass=True`
        and silently poison downstream audit consumers.
        """
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event("session_consolidated", **{field_name: True})
        ok, reason = _validate_event_schema(event)
        assert ok is False
        assert field_name in reason
        assert "must be int" in reason
        assert "got bool" in reason

    @pytest.mark.parametrize("field_name", ["pass", "task_count", "memories_saved"])
    def test_session_consolidated_int_field_rejects_str(self, field_name):
        """str is rejected as int for each optional field."""
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event("session_consolidated", **{field_name: "2"})
        ok, reason = _validate_event_schema(event)
        assert ok is False
        assert field_name in reason
        assert "must be int" in reason
        assert "got str" in reason

    def test_session_consolidated_all_fields_happy_path(self):
        """Full payload with all three optional fields validates."""
        from shared.session_journal import _validate_event_schema, make_event

        event = make_event(
            "session_consolidated",
            **{"pass": 2, "task_count": 7, "memories_saved": 3},
        )
        ok, reason = _validate_event_schema(event)
        assert ok is True, f"full payload should pass; got {reason!r}"
        assert reason == "ok"
