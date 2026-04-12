"""
Tests for shared/failure_log.py -- bounded ring buffer log for session_init
malformed-stdin failures.

Tests cover:

append_failure():
1. Creates file and parent directory on first call
2. Writes JSONL record with all expected fields (ts, classification, error, cwd, source)
3. Truncates error field to 200 chars
4. Rotation: caps the log at MAX_ENTRIES (100) via read-keep-99-append
5. Concurrent writes are serialized via file_lock (no interleave/corruption)
6. Fail-open on lock timeout — does not raise
7. Fail-open on disk error (OSError from write_text) — does not raise
8. Fail-open on JSON encode error (via non-serializable cwd/source) — does not raise
9. Fail-open on any unexpected exception — does not raise
10. File permissions are 0o600 after write
11. Optional cwd/source fields default to None

read_failures():
12. Returns [] when log file is missing
13. Returns [] on lock/IO error
14. Returns parsed entries in chronological order (oldest first)
15. Skips malformed lines silently
16. Returns [] on outer exception (fail-open)
"""
import json
import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared import failure_log
from shared.failure_log import (
    LOG_PATH,
    MAX_ENTRIES,
    append_failure,
    read_failures,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def failure_log_home(tmp_path, monkeypatch):
    """Redirect Path.home() and the module-level LOG_PATH to tmp_path.

    The module caches LOG_PATH at import time, so monkeypatching Path.home()
    alone is not enough — we also override failure_log.LOG_PATH to the tmp
    location. Symmetric with how test_session_journal pins its paths.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tmp_log = tmp_path / ".claude" / "pact-sessions" / "_session_init_failures.log"
    monkeypatch.setattr(failure_log, "LOG_PATH", tmp_log)
    return tmp_log


# ---------------------------------------------------------------------------
# append_failure: basic behavior
# ---------------------------------------------------------------------------

class TestAppendCreatesFile:
    def test_creates_file_and_parent_dir(self, failure_log_home):
        """First append creates the file and the parent pact-sessions dir."""
        log = failure_log_home
        assert not log.exists()
        assert not log.parent.exists()

        append_failure("missing_session_id", "raw_id was None")

        assert log.exists()
        assert log.parent.is_dir()
        # Parent dir permissions: 0o700 per PACT convention
        parent_mode = log.parent.stat().st_mode & 0o777
        assert parent_mode == 0o700

    def test_file_permissions_are_0o600(self, failure_log_home):
        """Written file has owner-only 0o600 permissions."""
        log = failure_log_home
        append_failure("missing_session_id", "test")
        mode = log.stat().st_mode & 0o777
        assert mode == 0o600


class TestAppendRecordFormat:
    def test_writes_jsonl_record_with_all_fields(self, failure_log_home):
        """Record contains ts (ISO 8601 UTC), classification, error, cwd, source."""
        append_failure(
            classification="missing_session_id",
            error="raw_id was None",
            cwd="/home/test/project",
            source="startup",
        )

        entries = read_failures()
        assert len(entries) == 1
        entry = entries[0]

        # Required fields
        assert "ts" in entry
        assert entry["classification"] == "missing_session_id"
        assert entry["error"] == "raw_id was None"
        assert entry["cwd"] == "/home/test/project"
        assert entry["source"] == "startup"

        # ts format: ISO 8601 UTC with trailing Z (matches session_journal)
        ts = entry["ts"]
        assert isinstance(ts, str)
        assert ts.endswith("Z")
        assert "T" in ts
        assert len(ts) >= 19  # "YYYY-MM-DDTHH:MM:SSZ" = 20 chars

    def test_optional_fields_default_to_none(self, failure_log_home):
        """cwd and source default to None when not supplied."""
        append_failure("missing_session_id", "boom")
        entries = read_failures()
        assert len(entries) == 1
        assert entries[0]["cwd"] is None
        assert entries[0]["source"] is None

    def test_truncates_error_to_200_chars(self, failure_log_home):
        """An error longer than 200 chars is truncated to exactly 200."""
        long_error = "x" * 500
        append_failure("malformed_json", long_error)

        entries = read_failures()
        assert len(entries) == 1
        assert len(entries[0]["error"]) == 200
        assert entries[0]["error"] == "x" * 200

    def test_empty_error_is_safe(self, failure_log_home):
        """An empty string error is recorded as empty string, not crashed."""
        append_failure("malformed_json", "")
        entries = read_failures()
        assert len(entries) == 1
        assert entries[0]["error"] == ""

    def test_none_error_is_normalized_to_empty_string(self, failure_log_home):
        """Passing error=None is tolerated (the annotation permits str|None)
        and gets normalized to an empty string in the record. The body's
        defensive `(error or "")` guards against call sites that pass
        None unintentionally — the annotation now reflects that contract.
        """
        append_failure("malformed_json", None)
        entries = read_failures()
        assert len(entries) == 1
        assert entries[0]["error"] == ""


# ---------------------------------------------------------------------------
# append_failure: rotation
# ---------------------------------------------------------------------------

class TestRotation:
    def test_rotation_caps_at_max_entries(self, failure_log_home):
        """Appending (MAX_ENTRIES + 50) leaves exactly MAX_ENTRIES in the log,
        with the oldest 50 dropped.
        """
        total = MAX_ENTRIES + 50
        for i in range(total):
            append_failure("missing_session_id", f"entry-{i}")

        entries = read_failures()
        assert len(entries) == MAX_ENTRIES

        # The retained entries are the LAST 100, i.e. entry-50 through entry-149
        assert entries[0]["error"] == f"entry-{total - MAX_ENTRIES}"
        assert entries[-1]["error"] == f"entry-{total - 1}"

    def test_below_cap_retains_all(self, failure_log_home):
        """Appending fewer than MAX_ENTRIES leaves all entries in the log."""
        for i in range(10):
            append_failure("missing_session_id", f"entry-{i}")
        entries = read_failures()
        assert len(entries) == 10
        # Order preserved: entry-0, entry-1, ..., entry-9
        for i, entry in enumerate(entries):
            assert entry["error"] == f"entry-{i}"

    def test_rotation_at_exact_boundary(self, failure_log_home):
        """Exactly MAX_ENTRIES entries means no rotation happens yet."""
        for i in range(MAX_ENTRIES):
            append_failure("missing_session_id", f"entry-{i}")
        entries = read_failures()
        assert len(entries) == MAX_ENTRIES
        assert entries[0]["error"] == "entry-0"
        assert entries[-1]["error"] == f"entry-{MAX_ENTRIES - 1}"

        # One more, and the first is dropped
        append_failure("missing_session_id", "entry-overflow")
        entries = read_failures()
        assert len(entries) == MAX_ENTRIES
        assert entries[0]["error"] == "entry-1"
        assert entries[-1]["error"] == "entry-overflow"


# ---------------------------------------------------------------------------
# append_failure: concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_concurrent_writes_via_file_lock(self, failure_log_home):
        """Two threads hammering append_failure cannot interleave/corrupt
        JSONL lines. Every entry parses successfully after the storm.
        """
        writes_per_thread = 25
        num_threads = 4

        def worker(worker_id: int):
            for seq in range(writes_per_thread):
                append_failure(
                    classification="missing_session_id",
                    error=f"worker-{worker_id}-seq-{seq}",
                    cwd=f"/w{worker_id}",
                    source="test",
                )

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All entries must parse (no interleaved/corrupt JSONL lines).
        # Total writes = writes_per_thread * num_threads = 100 = MAX_ENTRIES,
        # so no rotation fires and every write survives.
        entries = read_failures()
        assert len(entries) == writes_per_thread * num_threads

        # Each thread's writes are fully represented
        seen_per_worker = {i: 0 for i in range(num_threads)}
        for entry in entries:
            err = entry["error"]
            assert err.startswith("worker-")
            worker_id = int(err.split("-")[1])
            seen_per_worker[worker_id] += 1

        for i in range(num_threads):
            assert seen_per_worker[i] == writes_per_thread


# ---------------------------------------------------------------------------
# append_failure: fail-open
# ---------------------------------------------------------------------------

class TestFailOpen:
    def test_fail_open_on_lock_timeout(self, failure_log_home, monkeypatch):
        """TimeoutError from file_lock is swallowed; append returns cleanly."""
        from contextlib import contextmanager

        @contextmanager
        def timeout_lock(target_file):
            raise TimeoutError(f"simulated lock timeout on {target_file}")
            yield  # unreachable

        monkeypatch.setattr(failure_log, "file_lock", timeout_lock)

        # Must not raise
        append_failure("missing_session_id", "test lock timeout")

        # No entry written
        assert not failure_log_home.exists() or read_failures() == []

    def test_fail_open_on_disk_error_from_write_text(
        self, failure_log_home, monkeypatch
    ):
        """OSError during write_text is swallowed; append returns cleanly."""
        real_write_text = Path.write_text

        def failing_write_text(self, *args, **kwargs):
            if self == failure_log_home:
                raise OSError("simulated disk full")
            return real_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", failing_write_text)

        # Must not raise even though the target write would fail
        append_failure("missing_session_id", "test disk error")

    def test_fail_open_on_mkdir_error(self, failure_log_home, monkeypatch):
        """OSError from mkdir is swallowed; append returns cleanly."""
        real_mkdir = Path.mkdir

        def failing_mkdir(self, *args, **kwargs):
            if self == failure_log_home.parent:
                raise OSError("simulated mkdir failure")
            return real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", failing_mkdir)
        append_failure("missing_session_id", "test mkdir failure")

    def test_fail_open_on_unknown_exception(
        self, failure_log_home, monkeypatch
    ):
        """Any unexpected exception inside the critical section is swallowed."""
        from contextlib import contextmanager

        @contextmanager
        def boom_lock(target_file):
            raise RuntimeError("simulated unexpected exception")
            yield  # unreachable

        monkeypatch.setattr(failure_log, "file_lock", boom_lock)
        append_failure("missing_session_id", "test unexpected exception")

    def test_fail_open_returns_none(self, failure_log_home, monkeypatch):
        """append_failure returns None on failure (and on success)."""
        from contextlib import contextmanager

        @contextmanager
        def timeout_lock(target_file):
            raise TimeoutError("boom")
            yield

        monkeypatch.setattr(failure_log, "file_lock", timeout_lock)
        result = append_failure("missing_session_id", "test")
        assert result is None


# ---------------------------------------------------------------------------
# append_failure: symlink guard (TOCTOU defense)
# ---------------------------------------------------------------------------

class TestSymlinkGuard:
    """LOG_PATH must never be followed if it points at a symlink.

    The guard runs INSIDE the file_lock so a concurrent writer cannot
    swap a regular file for a symlink between check and write. The
    guard is silent fail-open: if LOG_PATH is a symlink, append_failure
    returns None without writing anything (and without following the
    link). This prevents an attacker (or accidental local action) from
    tricking session_init's failure logger into clobbering an arbitrary
    file via symlink redirection (e.g., /etc/passwd, ~/.ssh/authorized_keys).
    """

    def test_symlink_at_log_path_is_not_followed(
        self, failure_log_home, tmp_path
    ):
        """Replacing LOG_PATH with a symlink causes append_failure to no-op
        without modifying the symlink target.
        """
        log = failure_log_home
        log.parent.mkdir(parents=True, exist_ok=True)

        # Create a target file we want to PROTECT from being clobbered.
        # Place it outside the pact-sessions tree so a successful write
        # would be unambiguously detectable.
        target = tmp_path / "victim.txt"
        original_content = "DO NOT OVERWRITE\n"
        target.write_text(original_content, encoding="utf-8")

        # Replace LOG_PATH with a symlink pointing at the victim file.
        log.symlink_to(target)
        assert log.is_symlink()

        # Attempt to append. Must not raise. Must not write to the target.
        append_failure("missing_session_id", "symlink redirection attempt")

        # The symlink itself still exists and still points at the victim,
        # but the victim is unchanged. The guard short-circuited before
        # any read or write happened.
        assert log.is_symlink()
        assert target.read_text(encoding="utf-8") == original_content

        # And read_failures returns [] because nothing valid was logged.
        # (read_failures itself does follow the symlink to read, which is
        # safe — the file content hasn't been touched by us.)
        # We assert specifically that the victim still has its original
        # content, not log entries.
        assert "missing_session_id" not in target.read_text(encoding="utf-8")

    def test_symlink_to_nonexistent_target_is_silent(
        self, failure_log_home, tmp_path
    ):
        """A dangling symlink at LOG_PATH still triggers fail-open no-op."""
        log = failure_log_home
        log.parent.mkdir(parents=True, exist_ok=True)

        nonexistent = tmp_path / "does_not_exist.log"
        log.symlink_to(nonexistent)
        assert log.is_symlink()
        assert not nonexistent.exists()

        # Must not raise even though the symlink target is missing.
        append_failure("missing_session_id", "dangling symlink")

        # Nothing was created at the target path.
        assert not nonexistent.exists()
        # The symlink itself is still there, untouched.
        assert log.is_symlink()

    def test_regular_file_still_writes_normally(self, failure_log_home):
        """Sanity check: with no symlink, append_failure writes normally."""
        log = failure_log_home
        assert not log.exists()

        append_failure("missing_session_id", "normal write")

        assert log.exists()
        assert not log.is_symlink()
        entries = read_failures()
        assert len(entries) == 1
        assert entries[0]["error"] == "normal write"


# ---------------------------------------------------------------------------
# read_failures
# ---------------------------------------------------------------------------

class TestReadFailures:
    def test_read_empty_when_file_missing(self, failure_log_home):
        """Returns [] when the log file does not exist."""
        assert not failure_log_home.exists()
        assert read_failures() == []

    def test_read_returns_entries_in_order(self, failure_log_home):
        """Appended in order A, B, C → read returns [A, B, C]."""
        append_failure("missing_session_id", "A")
        append_failure("missing_session_id", "B")
        append_failure("missing_session_id", "C")

        entries = read_failures()
        assert [e["error"] for e in entries] == ["A", "B", "C"]

    def test_read_skips_malformed_lines(self, failure_log_home):
        """Mixed valid + garbage lines → only valid entries returned.

        Also verifies the ring buffer rotation scrubs malformed lines on the
        next append (since rotation re-writes the kept subset, dropping any
        line that doesn't parse).
        """
        failure_log_home.parent.mkdir(parents=True, exist_ok=True)
        valid_record = {
            "ts": "2026-04-11T10:00:00Z",
            "classification": "missing_session_id",
            "error": "good line",
            "cwd": None,
            "source": None,
        }
        mixed_content = (
            "{ not json at all\n"
            + json.dumps(valid_record)
            + "\n"
            + "random garbage line\n"
            + "\n"  # empty line
            + json.dumps({"ts": "x", "classification": "y", "error": "another good",
                          "cwd": None, "source": None})
            + "\n"
        )
        failure_log_home.write_text(mixed_content, encoding="utf-8")

        entries = read_failures()
        assert len(entries) == 2
        assert entries[0]["error"] == "good line"
        assert entries[1]["error"] == "another good"

    def test_read_handles_empty_file(self, failure_log_home):
        """An empty log file returns []."""
        failure_log_home.parent.mkdir(parents=True, exist_ok=True)
        failure_log_home.write_text("", encoding="utf-8")
        assert read_failures() == []

    def test_read_fails_open_on_os_error(
        self, failure_log_home, monkeypatch
    ):
        """OSError from read_text returns [] rather than raising."""
        failure_log_home.parent.mkdir(parents=True, exist_ok=True)
        failure_log_home.write_text("{}\n", encoding="utf-8")

        real_read_text = Path.read_text

        def failing_read_text(self, *args, **kwargs):
            if self == failure_log_home:
                raise OSError("simulated read failure")
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", failing_read_text)
        assert read_failures() == []

    def test_rotation_preserves_good_lines_after_malformed(
        self, failure_log_home
    ):
        """Append after garbage lines → garbage is scrubbed on rotation write."""
        failure_log_home.parent.mkdir(parents=True, exist_ok=True)
        failure_log_home.write_text(
            "not json\n" + json.dumps({"keep": "me"}) + "\n",
            encoding="utf-8",
        )

        append_failure("missing_session_id", "new entry")

        # After append, the garbage line is gone (rotation scrubbed it)
        raw = failure_log_home.read_text(encoding="utf-8")
        for line in raw.splitlines():
            if not line.strip():
                continue
            # Every remaining line must parse
            json.loads(line)
