"""
Tests for shared/claude_md_manager.py -- CLAUDE.md file manipulation.

Tests cover:

ensure_project_memory_md() — project CLAUDE.md creation:
1. Returns None when CLAUDE_PROJECT_DIR not set
2. Returns None when project CLAUDE.md already exists (legacy ./CLAUDE.md)
3. Creates project CLAUDE.md (.claude/CLAUDE.md, new default) with memory sections
4. Created file contains session markers
5. Returns None when .claude/CLAUDE.md already exists (no overwrite)
6. Returns None when only legacy ./CLAUDE.md exists (no migration)
7. .claude/CLAUDE.md takes precedence when both locations exist
8. Created .claude/CLAUDE.md has 0o600 permissions; .claude/ dir 0o700

migrate_to_managed_structure() / _build_migrated_content() — legacy
project CLAUDE.md migration to the PACT_MANAGED boundary structure:
9. Wraps user content with PACT_MANAGED_START/END outer boundary
10. Wraps memory sections with PACT_MEMORY_START/END inner boundary
11. Preserves SESSION_START/END block
12. Strips legacy stale-orchestrator-loader template lines
13. Idempotent on already-migrated input
14. Symlink guard: refuses to operate on symlinks
"""

import os
import sys
from pathlib import Path

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# ---------------------------------------------------------------------------
# Marker literals \u2014 module-level constants used as fixture inputs
# ---------------------------------------------------------------------------
# These mirror the boundary markers used in the on-disk PACT_MANAGED format
# and are referenced by `_build_migrated_content` test fixtures. The routing
# markers are kept here as input data only \u2014 `_build_migrated_content` no
# longer special-cases them, so any input containing them is passed through
# as user content within the PACT_MEMORY region.

_ROUTING_START = "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
_ROUTING_END = "<!-- PACT_ROUTING_END -->"
_MANAGED_START = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
_MANAGED_END = "<!-- PACT_MANAGED_END -->"
_MEMORY_START = "<!-- PACT_MEMORY_START -->"
_MEMORY_END = "<!-- PACT_MEMORY_END -->"
_SESSION_START = "<!-- SESSION_START -->"
_SESSION_END = "<!-- SESSION_END -->"


# ---------------------------------------------------------------------------
# Shared fixture: mock Path.home() so tests never touch real ~/.claude
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_home(tmp_path, monkeypatch):
    """Patch Path.home() to return a tempdir-backed ~/.claude.

    Required for any test that exercises strip_orphan_kernel_block() or
    other functions that read/write under Path.home() / ".claude".
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


class TestEnsureProjectMemoryMd:
    """Tests for ensure_project_memory_md() -- project CLAUDE.md creation."""

    def test_returns_none_when_no_project_dir(self, monkeypatch):
        """Should return None when CLAUDE_PROJECT_DIR not set."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        result = ensure_project_memory_md()

        assert result is None

    def test_returns_none_when_file_exists(self, tmp_path, monkeypatch):
        """Should return None when project CLAUDE.md already exists."""
        from shared.claude_md_manager import ensure_project_memory_md

        (tmp_path / "CLAUDE.md").write_text("existing content")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = ensure_project_memory_md()

        assert result is None
        # Content should be unchanged
        assert (tmp_path / "CLAUDE.md").read_text() == "existing content"

    def test_creates_project_claude_md(self, tmp_path, monkeypatch):
        """Should create .claude/CLAUDE.md (new default) with memory sections."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = ensure_project_memory_md()

        assert result == "Created project CLAUDE.md with memory sections"
        new_default = tmp_path / ".claude" / "CLAUDE.md"
        legacy = tmp_path / "CLAUDE.md"
        assert new_default.exists()
        assert not legacy.exists()
        content = new_default.read_text()
        assert "# PACT Framework and Managed Project Memory" in content
        assert "## Retrieved Context" in content
        assert "## Working Memory" in content

    def test_created_file_contains_session_markers(self, tmp_path, monkeypatch):
        """Should include session markers in the created file."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        assert "<!-- SESSION_START -->" in content
        assert "<!-- SESSION_END -->" in content

    def test_returns_none_when_dot_claude_exists(self, tmp_path, monkeypatch):
        """Should return None and not overwrite when .claude/CLAUDE.md already exists."""
        from shared.claude_md_manager import ensure_project_memory_md

        dot_claude_dir = tmp_path / ".claude"
        dot_claude_dir.mkdir()
        dot_claude_file = dot_claude_dir / "CLAUDE.md"
        dot_claude_file.write_text("existing dot-claude content")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = ensure_project_memory_md()

        assert result is None
        assert dot_claude_file.read_text() == "existing dot-claude content"
        # Legacy was not created as a side effect
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_returns_none_when_legacy_exists(self, tmp_path, monkeypatch):
        """Should return None when only the legacy ./CLAUDE.md exists (no migration)."""
        from shared.claude_md_manager import ensure_project_memory_md

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("existing legacy content")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = ensure_project_memory_md()

        assert result is None
        # Legacy file is preserved as-is; no migration to .claude/
        assert legacy.read_text() == "existing legacy content"
        assert not (tmp_path / ".claude").exists()

    def test_dot_claude_takes_precedence_over_legacy(self, tmp_path, monkeypatch):
        """When both exist, .claude/CLAUDE.md is preferred (return None, no edit)."""
        from shared.claude_md_manager import ensure_project_memory_md

        dot_claude_dir = tmp_path / ".claude"
        dot_claude_dir.mkdir()
        dot_claude_file = dot_claude_dir / "CLAUDE.md"
        dot_claude_file.write_text("preferred")
        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("legacy")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = ensure_project_memory_md()

        assert result is None
        assert dot_claude_file.read_text() == "preferred"
        assert legacy.read_text() == "legacy"

    def test_created_file_has_secure_permissions(self, tmp_path, monkeypatch):
        """Newly created .claude/CLAUDE.md should be 0o600; .claude/ dir should be 0o700."""
        import stat
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        ensure_project_memory_md()

        new_default = tmp_path / ".claude" / "CLAUDE.md"
        assert new_default.exists()
        file_mode = stat.S_IMODE(new_default.stat().st_mode)
        assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"
        dir_mode = stat.S_IMODE(new_default.parent.stat().st_mode)
        assert dir_mode == 0o700, f"Expected 0o700, got {oct(dir_mode)}"


class TestEnsureProjectMemoryMdErrorPaths:
    """Tests for ensure_project_memory_md() exception handling."""

    def test_returns_error_message_on_write_failure(self, tmp_path, monkeypatch):
        """Should return truncated error message when write fails."""
        from shared.claude_md_manager import ensure_project_memory_md

        # Point to a directory where we can't write
        read_only = tmp_path / "readonly"
        read_only.mkdir()

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(read_only))

        from unittest.mock import patch
        with patch.object(Path, "write_text", side_effect=OSError("No space left")):
            result = ensure_project_memory_md()

        assert result is not None
        assert "Project CLAUDE.md failed:" in result

    def test_lock_timeout_returns_skip_message(self, tmp_path, monkeypatch):
        """C: when file_lock raises TimeoutError, ensure_project_memory_md
        must return the human-readable skip message and NOT create the file.

        Coverage gap closed: ensure_project_memory_md must fail-open on
        a stuck lock (concurrent session_init hooks) so session start
        skips the project CLAUDE.md creation gracefully instead of
        crashing.

        We monkeypatch the file_lock symbol on the claude_md_manager module
        directly to raise TimeoutError on entry — simpler than spinning up a
        threaded lock holder and isolates this test from the lock
        infrastructure's own contention semantics.
        """
        from contextlib import contextmanager
        from shared import claude_md_manager as cmm

        # Fresh empty project dir so the resolver returns "new_default" and
        # ensure_project_memory_md proceeds to the file_lock branch.
        project_dir = tmp_path / "fresh_project"
        project_dir.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        # Stub file_lock to raise TimeoutError on entry, mirroring how the
        # real implementation behaves when _LOCK_TIMEOUT_SECONDS elapses
        # without acquiring the sidecar lock.
        @contextmanager
        def timing_out_lock(_path):
            raise TimeoutError(
                "Failed to acquire lock on .CLAUDE.md.lock within 5s"
            )
            yield  # pragma: no cover  -- unreachable, contextmanager requires it

        monkeypatch.setattr(cmm, "file_lock", timing_out_lock)

        result = cmm.ensure_project_memory_md()

        # Result must be the human-readable skip message routed to systemMessage.
        assert result is not None
        assert "Failed to acquire lock" in result
        assert "5s" in result
        assert "skipped" in result
        assert "next session start" in result

        # The CLAUDE.md file must NOT have been created — the timeout aborts
        # the write before any filesystem mutation.
        assert not (project_dir / ".claude" / "CLAUDE.md").exists()
        assert not (project_dir / "CLAUDE.md").exists()


class TestResolveProjectClaudeMdPath:
    """Direct tests for resolve_project_claude_md_path() helper.

    The resolver returns (path, source) where source is one of:
      - "dot_claude": existing .claude/CLAUDE.md
      - "legacy": existing ./CLAUDE.md
      - "new_default": neither exists; path points to .claude/CLAUDE.md
    """

    def test_returns_dot_claude_when_only_dot_claude_exists(self, tmp_path):
        """Returns .claude/CLAUDE.md path with 'dot_claude' source."""
        from shared.claude_md_manager import resolve_project_claude_md_path

        dot_claude = tmp_path / ".claude" / "CLAUDE.md"
        dot_claude.parent.mkdir()
        dot_claude.write_text("# dot-claude")

        path, source = resolve_project_claude_md_path(tmp_path)

        assert path == dot_claude
        assert source == "dot_claude"

    def test_returns_legacy_when_only_legacy_exists(self, tmp_path):
        """Returns ./CLAUDE.md path with 'legacy' source."""
        from shared.claude_md_manager import resolve_project_claude_md_path

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# legacy")

        path, source = resolve_project_claude_md_path(tmp_path)

        assert path == legacy
        assert source == "legacy"

    def test_prefers_dot_claude_when_both_exist(self, tmp_path):
        """When both files exist, .claude/CLAUDE.md wins."""
        from shared.claude_md_manager import resolve_project_claude_md_path

        dot_claude = tmp_path / ".claude" / "CLAUDE.md"
        dot_claude.parent.mkdir()
        dot_claude.write_text("# preferred")
        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# legacy")

        path, source = resolve_project_claude_md_path(tmp_path)

        assert path == dot_claude
        assert source == "dot_claude"
        assert path != legacy

    def test_returns_new_default_when_neither_exists(self, tmp_path):
        """When neither file exists, points to .claude/CLAUDE.md as the new default."""
        from shared.claude_md_manager import resolve_project_claude_md_path

        path, source = resolve_project_claude_md_path(tmp_path)

        assert path == tmp_path / ".claude" / "CLAUDE.md"
        assert source == "new_default"
        # No filesystem side effects -- resolver only inspects, never creates
        assert not path.exists()
        assert not (tmp_path / ".claude").exists()

    def test_accepts_string_project_dir(self, tmp_path):
        """Accepts string paths in addition to Path objects."""
        from shared.claude_md_manager import resolve_project_claude_md_path

        path, source = resolve_project_claude_md_path(str(tmp_path))

        assert source == "new_default"
        assert path == tmp_path / ".claude" / "CLAUDE.md"


class TestEnsureDotClaudeParent:
    """Tests for ensure_dot_claude_parent() helper."""

    def test_creates_dot_claude_dir_with_secure_mode(self, tmp_path):
        """Creates the parent directory with mode 0o700."""
        import stat
        from shared.claude_md_manager import ensure_dot_claude_parent

        target = tmp_path / ".claude" / "CLAUDE.md"
        assert not target.parent.exists()

        ensure_dot_claude_parent(target)

        assert target.parent.exists()
        mode = stat.S_IMODE(target.parent.stat().st_mode)
        assert mode == 0o700, f"Expected 0o700, got {oct(mode)}"

    def test_no_op_when_parent_exists(self, tmp_path):
        """Does not raise when the parent directory already exists."""
        from shared.claude_md_manager import ensure_dot_claude_parent

        # Pre-create the parent (legacy path -- no .claude/ subdir)
        target = tmp_path / "CLAUDE.md"
        # tmp_path always exists; nothing to create
        ensure_dot_claude_parent(target)  # Should not raise

        assert tmp_path.exists()

    def test_creates_nested_parents(self, tmp_path):
        """Creates intermediate directories if needed (parents=True)."""
        from shared.claude_md_manager import ensure_dot_claude_parent

        # Simulate a deeper-than-expected layout (defensive)
        target = tmp_path / "outer" / ".claude" / "CLAUDE.md"

        ensure_dot_claude_parent(target)

        assert target.parent.exists()

    def test_raises_when_parent_is_regular_file(self, tmp_path):
        """Raises a clear OSError when `path.parent` exists but is a
        regular file instead of a directory.

        Without the is_dir guard, the failure would surface as a
        confusing late-stage OSError from `write_text` in
        ensure_project_memory_md. With the guard, callers catch the
        early OSError and return a clear failure status string.

        This is a pathological case (e.g., a local attacker blocks
        mkdir by planting a file at the `.claude/` path), but the
        early guard makes the failure mode readable.
        """
        import pytest
        from shared.claude_md_manager import ensure_dot_claude_parent

        # Create a regular file where `.claude/` would go
        blocker = tmp_path / ".claude"
        blocker.write_text("I am a file, not a directory", encoding="utf-8")
        assert blocker.exists()
        assert blocker.is_file()
        assert not blocker.is_dir()

        target = blocker / "CLAUDE.md"  # ensure_dot_claude_parent inspects target.parent

        with pytest.raises(OSError, match="exists but is not a directory"):
            ensure_dot_claude_parent(target)

        # The blocker file is untouched — guard is read-only
        assert blocker.read_text(encoding="utf-8") == "I am a file, not a directory"


class TestFileLockContextManager:
    """file_lock(target_file) acquires/releases an fcntl sidecar lock."""

    def test_sequential_acquisitions_work(self, tmp_path):
        """Acquire, release, re-acquire must succeed without blocking."""
        from shared.claude_md_manager import file_lock

        target = tmp_path / "CLAUDE.md"
        target.write_text("content", encoding="utf-8")

        with file_lock(target):
            pass  # Exited cleanly; lock released

        with file_lock(target):
            pass  # Second acquisition must not block or raise

        # Sidecar exists and is not cleaned up (by design)
        sidecar = tmp_path / ".CLAUDE.md.lock"
        assert sidecar.exists()

    def test_sidecar_path_shape(self, tmp_path):
        """Sidecar lock must be `{parent}/.{name}.lock` adjacent to target."""
        from shared.claude_md_manager import file_lock

        target = tmp_path / "CLAUDE.md"
        target.write_text("x", encoding="utf-8")

        with file_lock(target):
            sidecar = tmp_path / ".CLAUDE.md.lock"
            assert sidecar.exists()
            # Sidecar is NOT the target itself
            assert sidecar != target

    def test_sidecar_has_secure_permissions(self, tmp_path):
        """Sidecar lock file should be 0o600 to match CLAUDE.md permissions."""
        import stat
        from shared.claude_md_manager import file_lock

        target = tmp_path / "CLAUDE.md"
        target.write_text("x", encoding="utf-8")

        sidecar = tmp_path / ".CLAUDE.md.lock"
        if sidecar.exists():
            sidecar.unlink()

        with file_lock(target):
            pass

        mode = stat.S_IMODE(sidecar.stat().st_mode)
        # Allow umask to tighten but not widen the permissions
        assert mode & 0o077 == 0, (
            f"Sidecar lock leaks permissions to group/other: {oct(mode)}"
        )

    def test_concurrent_acquisition_blocks_then_succeeds(self, tmp_path):
        """Thread A holds the lock; Thread B's acquire blocks until A releases."""
        import threading
        import time as _time
        from shared.claude_md_manager import file_lock

        target = tmp_path / "CLAUDE.md"
        target.write_text("x", encoding="utf-8")

        holder_has_lock = threading.Event()
        holder_release = threading.Event()
        b_acquired_at: list[float] = []
        holder_released_at: list[float] = []

        def holder():
            with file_lock(target):
                holder_has_lock.set()
                # Hold the lock until main signals release
                holder_release.wait(timeout=5)
                holder_released_at.append(_time.monotonic())

        t = threading.Thread(target=holder)
        t.start()
        assert holder_has_lock.wait(timeout=2), "holder thread never acquired"

        # Kick off the waiter in a second thread so we can release the
        # holder while the waiter is blocked in acquire.
        def waiter():
            with file_lock(target):
                b_acquired_at.append(_time.monotonic())

        w = threading.Thread(target=waiter)
        w.start()

        # Give the waiter a brief moment to enter the acquire loop, then
        # release the holder.
        _time.sleep(0.3)
        holder_release.set()

        t.join(timeout=5)
        w.join(timeout=5)
        assert not t.is_alive()
        assert not w.is_alive()
        assert len(b_acquired_at) == 1, "waiter did not acquire after release"
        assert len(holder_released_at) == 1
        # Waiter's acquire must happen AFTER holder's release (ordering proof)
        assert b_acquired_at[0] >= holder_released_at[0] - 0.05, (
            "waiter acquired before holder released — lock ordering broken"
        )

    def test_timeout_raises_timeouterror(self, tmp_path, monkeypatch):
        """If the lock cannot be acquired within the timeout, TimeoutError."""
        import threading
        from shared.claude_md_manager import file_lock
        from shared import claude_md_manager as cmm

        target = tmp_path / "CLAUDE.md"
        target.write_text("x", encoding="utf-8")

        # Shrink the timeout so the test completes quickly
        monkeypatch.setattr(cmm, "_LOCK_TIMEOUT_SECONDS", 0.3)

        holder_has_lock = threading.Event()
        holder_release = threading.Event()

        def holder():
            with file_lock(target):
                holder_has_lock.set()
                holder_release.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        assert holder_has_lock.wait(timeout=2)

        # Second acquire must time out
        with pytest.raises(TimeoutError) as exc_info:
            with file_lock(target):
                pass

        assert "Failed to acquire lock" in str(exc_info.value)
        assert ".CLAUDE.md.lock" in str(exc_info.value)

        # Clean up the holder
        holder_release.set()
        t.join(timeout=5)

    def test_exception_in_body_releases_lock(self, tmp_path):
        """A raise inside `with file_lock(...)` must still release the lock."""
        from shared.claude_md_manager import file_lock

        target = tmp_path / "CLAUDE.md"
        target.write_text("x", encoding="utf-8")

        class _MarkerError(Exception):
            pass

        with pytest.raises(_MarkerError):
            with file_lock(target):
                raise _MarkerError("boom")

        # Re-acquire must succeed — if the finally clause didn't release,
        # this would deadlock until the default 5s timeout.
        with file_lock(target):
            pass

    def test_timeout_emits_stderr_warning(self, tmp_path, monkeypatch, capsys):
        """S8 (security-engineer-review): when the 5s acquire deadline
        expires, file_lock must emit a stderr warning before raising
        TimeoutError. Callers fail-open on TimeoutError (skip cleanup),
        so without the warning a stuck holder silently defers cleanup
        forever. The warning is observable in Claude Code's debug logs
        even though it doesn't surface in the user transcript.

        Forces the timeout path by monkeypatching fcntl.flock to always
        raise BlockingIOError + setting the timeout to 0 so the
        deadline trips on the first iteration.
        """
        import fcntl as fcntl_mod
        import shared.claude_md_manager as cmm
        from shared.claude_md_manager import file_lock

        target = tmp_path / "CLAUDE.md"
        target.write_text("x", encoding="utf-8")

        # Force the timeout deadline to trip immediately.
        monkeypatch.setattr(cmm, "_LOCK_TIMEOUT_SECONDS", 0)

        # Only fail the EXCLUSIVE acquire. The UNLOCK in the finally
        # clause must succeed so the TimeoutError surfaces cleanly
        # (otherwise BlockingIOError from LOCK_UN shadows it).
        original_flock = fcntl_mod.flock

        def fail_only_exclusive(fd, op):
            if op & fcntl_mod.LOCK_EX:
                raise BlockingIOError("simulated contention")
            return original_flock(fd, op)

        monkeypatch.setattr(fcntl_mod, "flock", fail_only_exclusive)

        with pytest.raises(TimeoutError):
            with file_lock(target):
                pass  # never reached

        captured = capsys.readouterr()
        # Stderr must mention the contract: "PACT file_lock timeout"
        # + the lock-target path so debug-log readers can attribute
        # the warning to a specific managed file.
        assert "PACT file_lock timeout" in captured.err
        assert str(target.parent / f".{target.name}.lock") in captured.err
        assert "falling open" in captured.err


class TestBuildMigratedContentCurrentFormat:
    """_build_migrated_content() with the standard pre-#404 CLAUDE.md layout.

    This is the most common input shape: has the # Project Memory heading,
    routing block, session block, and all three memory sections with real
    content under them.
    """

    CURRENT_FORMAT = (
        "# Project Memory\n"
        "\n"
        "This file contains project-specific memory managed by the PACT framework.\n"
        "\n"
        f"{_ROUTING_START}\n"
        "## PACT Routing\n"
        "\n"
        "Some routing instructions here.\n"
        f"{_ROUTING_END}\n"
        "\n"
        f"{_SESSION_START}\n"
        "## Current Session\n"
        "- Team: pact-abc123\n"
        f"{_SESSION_END}\n"
        "\n"
        "## Retrieved Context\n"
        "Some retrieved context.\n"
        "\n"
        "## Pinned Context\n"
        "\n"
        "### Important pin\n"
        "Pin content here.\n"
        "\n"
        "## Working Memory\n"
        "### 2026-04-12 21:00\n"
        "Some working memory entry.\n"
    )

    def test_output_has_managed_boundary(self):
        """Migrated output must start with PACT_MANAGED_START and contain PACT_MANAGED_END."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert result.startswith(_MANAGED_START)
        assert _MANAGED_END in result

    def test_output_has_memory_boundary(self):
        """Migrated output must contain PACT_MEMORY_START and PACT_MEMORY_END."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert _MEMORY_START in result
        assert _MEMORY_END in result

    def test_new_heading_replaces_old(self):
        """Legacy '# Project Memory' is replaced by the single canonical H1
        '# PACT Framework and Managed Project Memory'."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert "# PACT Framework and Managed Project Memory" in result
        # Old heading text should not survive as a top-level heading
        lines = result.splitlines()
        top_level_headings = [l for l in lines if l.startswith("# ") and not l.startswith("## ")]
        assert "# Project Memory" not in top_level_headings
        # Single-H1 invariant: only one top-level heading (no interior H1
        # inside PACT_MEMORY).
        assert len(top_level_headings) == 1

    def test_memory_sections_inside_memory_boundary(self):
        """Retrieved Context, Pinned Context, and Working Memory must appear
        between PACT_MEMORY_START and PACT_MEMORY_END.
        """
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]

        assert "## Retrieved Context" in memory_region
        assert "## Pinned Context" in memory_region
        assert "## Working Memory" in memory_region

    def test_memory_content_preserved(self):
        """Content under memory sections must survive migration."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert "Some retrieved context." in result
        assert "Pin content here." in result
        assert "Some working memory entry." in result

    def test_session_block_preserved(self):
        """The session block (between its markers) must survive migration."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert _SESSION_START in result
        assert _SESSION_END in result
        assert "pact-abc123" in result

    def test_stale_orchestrator_line_stripped(self):
        """The 'loaded from ~/.claude/CLAUDE.md' line must be removed."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert "project-specific memory managed by the PACT framework" not in result

    def test_marker_ordering(self):
        """Markers must appear in the correct order: MANAGED_START -> SESSION
        -> MEMORY_START -> memory sections -> MEMORY_END -> MANAGED_END.
        """
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        positions = {
            "managed_start": result.index(_MANAGED_START),
            "session_start": result.index(_SESSION_START),
            "session_end": result.index(_SESSION_END),
            "memory_start": result.index(_MEMORY_START),
            "memory_end": result.index(_MEMORY_END),
            "managed_end": result.index(_MANAGED_END),
        }

        assert positions["managed_start"] < positions["session_start"]
        assert positions["session_end"] < positions["memory_start"]
        assert positions["memory_start"] < positions["memory_end"]
        assert positions["memory_end"] < positions["managed_end"]

    def test_no_interior_h1_inside_memory_boundary(self):
        """With the single-H1 restructure (#404), PACT_MEMORY must not contain
        any top-level heading — memory sections begin directly with their H2
        headings. Prior to the restructure, an interior H1 ('# Project Memory
        (PACT-Managed)') lived inside the memory boundary; that has been
        dropped in favor of a single outer H1.
        """
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]

        memory_lines = memory_region.splitlines()
        h1_headings = [l for l in memory_lines if l.startswith("# ") and not l.startswith("## ")]
        assert h1_headings == [], (
            f"PACT_MEMORY region should have no H1 headings, found: {h1_headings}"
        )

    def test_pinned_context_sub_heading_preserved(self):
        """Sub-headings (### level) under memory sections must be preserved."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert "### Important pin" in result
        assert "### 2026-04-12 21:00" in result


class TestBuildMigratedContentMissingSections:
    """_build_migrated_content() with various sections absent."""

    def test_no_routing_block(self):
        """File with no routing block should still produce valid structure."""
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "Some context.\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        assert _MANAGED_START in result
        assert _MANAGED_END in result
        assert _MEMORY_START in result
        assert _MEMORY_END in result
        # No routing markers should appear
        assert _ROUTING_START not in result
        assert "Some context." in result

    def test_no_session_block(self):
        """File with no session block should still produce valid structure."""
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            f"{_ROUTING_START}\n"
            "## PACT Routing\n"
            "Routing content.\n"
            f"{_ROUTING_END}\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        assert _MANAGED_START in result
        assert _MANAGED_END in result
        assert _ROUTING_START in result
        assert _SESSION_START not in result

    def test_no_memory_sections(self):
        """File with no memory headings should get default memory sections."""
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            f"{_ROUTING_START}\n"
            "## PACT Routing\n"
            "Routing content.\n"
            f"{_ROUTING_END}\n"
        )

        result = _build_migrated_content(content)

        assert _MEMORY_START in result
        assert _MEMORY_END in result
        # Default sections should be created
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "## Retrieved Context" in memory_region
        assert "## Pinned Context" in memory_region
        assert "## Working Memory" in memory_region

    def test_empty_content(self):
        """Empty string input should produce a minimal valid structure."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content("")

        assert _MANAGED_START in result
        assert _MANAGED_END in result
        assert _MEMORY_START in result
        assert _MEMORY_END in result
        assert "# PACT Framework and Managed Project Memory" in result

    def test_only_heading_no_sections(self):
        """Just the heading line, nothing else."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content("# Project Memory\n")

        assert _MANAGED_START in result
        assert _MANAGED_END in result
        assert _MEMORY_START in result
        assert "## Retrieved Context" in result  # default sections


class TestBuildMigratedContentUserContent:
    """_build_migrated_content() must preserve user content outside PACT sections."""

    def test_user_content_after_memory_sections(self):
        """User-owned sections after the last memory section must appear
        after PACT_MANAGED_END.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
            "\n"
            "## My Custom Section\n"
            "User's custom notes here.\n"
        )

        result = _build_migrated_content(content)

        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## My Custom Section" in after_managed
        assert "User's custom notes here." in after_managed

    def test_user_content_between_memory_sections(self):
        """A non-memory heading between memory sections splits into user content."""
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "Some context.\n"
            "\n"
            "## User Notes\n"
            "Private user notes.\n"
            "\n"
            "## Working Memory\n"
            "Working memory data.\n"
        )

        result = _build_migrated_content(content)

        # User notes should be outside managed block
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## User Notes" in after_managed
        assert "Private user notes." in after_managed

        # Memory sections should be inside memory boundary
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "## Retrieved Context" in memory_region
        assert "## Working Memory" in memory_region
        assert "Working memory data." in memory_region

    def test_user_content_before_memory_sections(self):
        """Content before any memory heading (after routing/session extraction)
        is classified as user content.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## My Early Section\n"
            "Early user content.\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## My Early Section" in after_managed
        assert "Early user content." in after_managed

    def test_user_h1_heading_survives_migration(self):
        """A user-owned H1 heading (e.g., '# My Project Notes') must be
        preserved outside the PACT_MANAGED block after migration.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Working Memory\n"
            "\n"
            "# My Project Notes\n"
            "Important notes the user added.\n"
        )

        result = _build_migrated_content(content)

        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "# My Project Notes" in after_managed
        assert "Important notes the user added." in after_managed

    def test_trailing_user_content_still_below_pact_managed(self):
        """Round 6 item 4: user content APPENDED after memory sections
        must still land BELOW ``PACT_MANAGED_END`` (the existing behavior).
        The preamble fix only moves PRE-memory user content; it must not
        regress POST-memory user content placement.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Working Memory\n"
            "\n"
            "## Trailing Notes\n"
            "Stuff the user appended later.\n"
        )

        result = _build_migrated_content(content)

        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## Trailing Notes" in after_managed
        assert "Stuff the user appended later." in after_managed

        # And it must NOT have migrated into the preamble region
        managed_start_idx = result.index(_MANAGED_START)
        before_managed = result[:managed_start_idx]
        assert "## Trailing Notes" not in before_managed
        assert "Stuff the user appended later." not in before_managed

class TestBuildMigratedContentAdversarial:
    """Adversarial and edge-case inputs for _build_migrated_content().

    Tests the MEDIUM uncertainty flagged by the coder: user headings that
    match memory section names could be mis-classified.
    """

    def test_user_heading_matching_memory_name_exact(self):
        """A user heading that exactly matches a memory section name
        (e.g., '## Retrieved Context') is classified as a memory section.

        This is the expected behavior — the classifier uses heading text
        to identify memory sections. Users should not have headings with
        these exact names outside the memory area.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "PACT-managed retrieval data.\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "PACT-managed retrieval data." in memory_region

    def test_similar_but_different_heading_not_captured(self):
        """Headings that are similar but not exact matches should NOT be
        classified as memory sections (e.g., '## Retrieved Context (old)').
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context (old)\n"
            "User's old retrieval notes.\n"
            "\n"
            "## Retrieved Context\n"
            "Actual PACT context.\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        # The exact-match section should be in memory
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "Actual PACT context." in memory_region

        # The near-match should be user content
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## Retrieved Context (old)" in after_managed
        assert "User's old retrieval notes." in after_managed

    def test_duplicate_memory_headings(self):
        """If '## Working Memory' appears twice, both instances and their
        content should end up in the memory region.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Working Memory\n"
            "First working memory block.\n"
            "\n"
            "## Working Memory\n"
            "Second working memory block.\n"
        )

        result = _build_migrated_content(content)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "First working memory block." in memory_region
        assert "Second working memory block." in memory_region

    def test_content_with_no_headings_at_all(self):
        """Flat text with no headings and no PACT-managed triggers is
        treated as user content and lands BELOW ``PACT_MANAGED_END``.

        Round 10 design decision: all user content migrates below the
        managed block. The prior (round 6) preamble mechanism placed such
        content above MANAGED_START, but that required fence-awareness in
        every downstream parser. Removing preamble handling eliminates
        the fence-awareness bug class at the cost of this one-time
        content relocation.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = "Just some random text in a CLAUDE.md file.\nAnother line.\n"

        result = _build_migrated_content(content)

        assert _MANAGED_START in result
        assert _MANAGED_END in result
        # User content lands BELOW MANAGED_END (round 10 contract)
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "Just some random text" in after_managed
        assert "Another line." in after_managed

        # And it must NOT appear ABOVE MANAGED_START
        managed_start_idx = result.index(_MANAGED_START)
        before_managed = result[:managed_start_idx]
        assert "Just some random text" not in before_managed

    def test_partial_routing_markers_no_end(self):
        """If only PACT_ROUTING_START is present with no END, the routing
        block regex won't match, so the marker text remains as-is in the
        remaining content (treated as user text).
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            f"{_ROUTING_START}\n"
            "Orphaned routing content.\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        # With no matching END marker, routing extraction fails — the
        # start marker and its content flow into user_parts
        assert _MANAGED_START in result
        assert _MANAGED_END in result

    def test_partial_session_markers_start_only(self):
        """If only SESSION_START is present with no SESSION_END, the session
        block regex won't match, so the marker text remains in the remaining
        content. Must not crash or corrupt the output.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            f"{_SESSION_START}\n"
            "## Current Session\n"
            "- Team: pact-orphaned\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        # Output must still have valid structure
        assert _MANAGED_START in result
        assert _MANAGED_END in result
        assert _MEMORY_START in result
        assert _MEMORY_END in result
        # The orphaned SESSION_START text should survive somewhere
        assert "pact-orphaned" in result

    def test_memory_heading_with_trailing_whitespace(self):
        """'## Retrieved Context   ' (trailing spaces) must still match
        as a memory heading since the code uses line.rstrip().
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context   \n"
            "Context data.\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "Context data." in memory_region

    def test_large_content_under_pinned_context(self):
        """Pinned Context with multiple sub-sections and substantial content
        must all be preserved inside the memory boundary.
        """
        from shared.claude_md_manager import _build_migrated_content

        pinned_content = "\n".join(
            [f"### Pin {i}\nContent for pin {i}.\n" for i in range(10)]
        )
        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            f"{pinned_content}\n"
            "## Working Memory\n"
        )

        result = _build_migrated_content(content)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        for i in range(10):
            assert f"Content for pin {i}." in memory_region

    def test_code_fenced_memory_heading_preserved_as_user_content(self):
        """A memory heading like `## Pinned Context` inside a fenced code
        block (```...```) must NOT be extracted as a real memory section.
        It is example/documentation text and belongs with surrounding user
        content.

        Regression guard for round-4 Item 3: the classifier previously did
        not track code fence state, so fenced `## Pinned Context` inside a
        user docs block was mis-classified as a memory section boundary.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Notes on how memory works\n"
            "Here's an example of what a pinned context block looks like:\n"
            "\n"
            "```markdown\n"
            "## Pinned Context\n"
            "This is example documentation, not real memory data.\n"
            "```\n"
            "\n"
            "End of notes.\n"
        )

        result = _build_migrated_content(content)

        # The fenced `## Pinned Context` text must survive outside the
        # PACT_MANAGED region as user content.
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## Notes on how memory works" in after_managed
        assert "```markdown" in after_managed
        assert "## Pinned Context" in after_managed
        assert "example documentation, not real memory data" in after_managed
        assert "End of notes." in after_managed

        # The memory region must be empty (no real memory headings existed).
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "example documentation" not in memory_region
        assert "End of notes." not in memory_region

    def test_code_fence_does_not_mask_real_memory_sections_elsewhere(self):
        """A fenced example of a memory heading in docs PLUS a real memory
        section elsewhere must still classify each correctly: the fenced one
        stays with user content, the real one is extracted into memory.

        Regression guard for round-4 Item 3: the fence toggle state must be
        per-fence, not latched — after a fence closes, subsequent real memory
        headings must still be detected.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Documentation\n"
            "Example of a memory heading:\n"
            "\n"
            "```\n"
            "## Working Memory\n"
            "(this is just an example)\n"
            "```\n"
            "\n"
            "## Retrieved Context\n"
            "Real retrieved context data.\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
            "- Real entry 1\n"
        )

        result = _build_migrated_content(content)

        # Fenced example must stay outside managed boundary as user content
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## Documentation" in after_managed
        assert "(this is just an example)" in after_managed

        # Real memory sections after the fence must be extracted into memory
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "Real retrieved context data." in memory_region
        assert "Real entry 1" in memory_region

        # The fenced example text must NOT bleed into the memory region
        assert "(this is just an example)" not in memory_region

    def test_fenced_stale_orchestrator_line_preserved(self):
        """Round 7 item 2 / round 8 item 4: `_strip_legacy_lines` must be
        fence-aware, and the regression test must exercise the POST-CUTOFF
        stripper path (not only the preamble-only path).

        Adversarial scenario: a user's CLAUDE.md has a fenced code block
        inside the managed region (e.g., inside `## Working Memory`) that
        quotes the legacy v3.16.2 orchestrator-loader line verbatim as
        migration documentation. The line is NOT part of the live PACT
        config — it's an example inside a fenced code block.

        Pre-round-7 behavior (verify-backend-coder-7 counter-test):
          `_STALE_ORCHESTRATOR_LINE_RE` was compiled with `re.MULTILINE`
          and applied via `_STALE_ORCHESTRATOR_LINE_RE.sub("", content)`
          against the full content. `^...$\\n?` matched every occurrence
          at any line boundary, INCLUDING lines inside user-authored
          fenced code blocks.

        Post-round-7: `_strip_legacy_lines` walks lines, tracks in-fence
        state via `^\\s*```` (round 7), and round 8 item 1 adds tilde
        fence state independently. Lines inside EITHER fence type are
        preserved byte-for-byte.

        Round 8 item 4 fixture fix: the pre-round-8 fixture had NO
        non-fenced PACT trigger — the fenced `# Project Memory` heading
        was the ONLY trigger in the file, and it was inside a fence, so
        `_find_preamble_cutoff` (also fence-aware) returned
        `len(content)`. That meant `remaining` was empty and the
        post-cutoff `_strip_legacy_lines` call NEVER saw the fenced
        content — only the preamble_text call did. Since the fenced line
        was in the preamble, the preamble-only stripping was enough.
        Round-8 audit caught that the counter-test-by-revert claim in
        the original docstring only exercised ONE of the two
        `_strip_legacy_lines` call sites.

        The new fixture places the fenced stale line INSIDE `## Working
        Memory`, with a non-fenced `# Project Memory` heading in
        position 0. `_find_preamble_cutoff` returns 0 → preamble is
        empty → full file is `remaining` → the post-cutoff
        `_strip_legacy_lines` call at line ~1330 is exercised by the
        fenced content. Reverting `_strip_legacy_lines` to the
        fence-unaware form now fails this test via the post-cutoff
        path, not the preamble path.

        Counter-test-by-revert validated for this regression: temporarily
        reverting `_strip_legacy_lines` to the fence-unaware
        `_STALE_ORCHESTRATOR_LINE_RE.sub` form makes this test fail
        (the fenced line disappears from the `## Working Memory`
        section's body inside the PACT_MANAGED region).
        """
        from shared.claude_md_manager import _build_migrated_content

        stale_line = (
            "The global PACT Orchestrator is loaded from "
            "`~/.claude/CLAUDE.md`."
        )

        # Non-fenced `# Project Memory` at position 0 → preamble cutoff is 0
        # → post-cutoff `remaining` is the full file body → fenced stale
        # line is scrubbed by the POST-cutoff `_strip_legacy_lines` call,
        # not the preamble-only call. This is the critical difference from
        # the pre-round-8 fixture.
        content = (
            "# Project Memory\n"
            "\n"
            "## Working Memory\n"
            "\n"
            "Migration notes for future reference:\n"
            "\n"
            "```markdown\n"
            "This is what the old v3.16.2 template looked like:\n"
            f"{stale_line}\n"
            "```\n"
            "\n"
            "The line inside the fence is an EXAMPLE, not live config.\n"
        )

        result = _build_migrated_content(content)

        # The PACT_MANAGED block must exist (the migration still runs).
        assert _MANAGED_START in result
        assert _MANAGED_END in result

        managed_start_idx = result.index(_MANAGED_START)
        managed_end_idx = result.index(_MANAGED_END)
        before_managed = result[:managed_start_idx]
        managed_region = result[managed_start_idx:managed_end_idx]
        after_managed = result[managed_end_idx:]

        # (a) PRIMARY ASSERTION — the fenced stale line MUST survive
        # verbatim INSIDE the managed region's `## Working Memory`
        # section. Pre-round-7 it was destroyed by the post-cutoff
        # `_STALE_ORCHESTRATOR_LINE_RE.sub`. Pre-round-8-fixture the
        # test couldn't detect this specific failure because the fenced
        # content was in the preamble path, not the remaining path.
        assert stale_line in managed_region, (
            "Fenced stale-orchestrator line must survive byte-for-byte "
            "inside the managed region. If this fails, the post-cutoff "
            "`_strip_legacy_lines` call at line ~1330 is still "
            "fence-unaware."
        )

        # (b) The fence boundaries survive intact in the managed region.
        fence_open_idx = managed_region.find("```markdown")
        assert fence_open_idx != -1, (
            "Opening ```markdown fence must survive in managed region"
        )
        fence_close_idx = managed_region.find("```\n", fence_open_idx + 1)
        assert fence_close_idx != -1, (
            "Closing ``` fence must survive in managed region"
        )
        assert fence_open_idx < fence_close_idx, (
            "Closing fence must appear after opening fence"
        )

        # (c) The stale line must appear BETWEEN the opening and closing
        # fences inside the managed region — not orphaned elsewhere, and
        # not stripped from its position inside the fence.
        stale_idx = managed_region.find(stale_line)
        assert fence_open_idx < stale_idx < fence_close_idx, (
            "Stale line must remain INSIDE the fence region inside the "
            "managed block, not be relocated or stripped"
        )

        # (d) The non-fenced narrative prose around the fence survives
        # too (inside the managed region's Working Memory section).
        assert "Migration notes for future reference:" in managed_region
        assert (
            "The line inside the fence is an EXAMPLE, not live config."
            in managed_region
        )

        # (e) The file had NO preamble (first line was `# Project
        # Memory`), so there should be no user content above
        # PACT_MANAGED_START.
        assert stale_line not in before_managed
        assert "```markdown" not in before_managed

        # (f) The trailing user region (below PACT_MANAGED_END) is also
        # empty of the fenced content.
        assert stale_line not in after_managed
        assert "```markdown" not in after_managed


    def test_tilde_fenced_memory_heading_classified_as_user_content(self):
        """PR #404 round 12 item 1: tilde-fenced ``## Working Memory``
        inside user content must NOT be extracted as a real memory section.

        The prior body classifier used a backtick-only boolean toggle
        (``in_code_fence``). Tilde fences (``~~~~``) are a valid CommonMark
        §4.5 alternative that the classifier must recognize.

        Counter-test-by-revert validated: reverting to the boolean toggle
        causes this test to fail because the tilde fence is invisible to
        ``startswith("```")``, so the ``## Working Memory`` heading inside
        the tilde fence is misclassified as a memory section boundary.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## User Notes\n"
            "Example of a memory section in tilde fence:\n"
            "\n"
            "~~~~markdown\n"
            "## Working Memory\n"
            "This is example text inside a tilde fence.\n"
            "~~~~\n"
            "\n"
            "End of user notes.\n"
        )

        result = _build_migrated_content(content)

        # Fenced content must land as user content below MANAGED_END
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## User Notes" in after_managed
        assert "~~~~markdown" in after_managed
        assert "## Working Memory" in after_managed
        assert "example text inside a tilde fence" in after_managed
        assert "End of user notes." in after_managed

        # Memory region must not contain the fenced heading
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "example text inside a tilde fence" not in memory_region

    def test_nested_4backtick_fence_does_not_toggle_on_inner_3backtick(self):
        """PR #404 round 12 item 1: a ```````` outer fence containing an
        inner ````` must not toggle the fence state.

        CommonMark §4.5: closing fence must have run length >= opening.
        A 4-backtick outer fence containing a 3-backtick inner example
        line must stay open through the inner line. A boolean toggle
        would falsely close on the inner ````` and expose the remainder
        of the outer fence body to heading classification.

        Counter-test-by-revert validated: reverting to the boolean toggle
        causes the inner ````` to close the fence, so ``## Pinned Context``
        on the next line is misclassified as a memory section boundary.
        """
        from shared.claude_md_manager import _build_migrated_content

        content = (
            "# Project Memory\n"
            "\n"
            "## Migration Guide\n"
            "Example with nested fences:\n"
            "\n"
            "````markdown\n"
            "Here is a code block:\n"
            "```\n"
            "## Pinned Context\n"
            "Some inner content\n"
            "```\n"
            "End of inner example.\n"
            "````\n"
            "\n"
            "Post-fence user notes.\n"
        )

        result = _build_migrated_content(content)

        # All fenced content must land as user content below MANAGED_END
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "## Migration Guide" in after_managed
        assert "````markdown" in after_managed
        assert "## Pinned Context" in after_managed
        assert "Some inner content" in after_managed
        assert "Post-fence user notes." in after_managed

        # Memory region must not contain the fenced heading
        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]
        assert "Some inner content" not in memory_region


class TestBuildMigratedContentIdempotent:
    """_build_migrated_content() has its own idempotency guard (round 5 item 2).

    The guard checks for MANAGED_START_MARKER at the top of the function and
    returns the content unchanged if already migrated. The integration wrapper
    migrate_to_managed_structure also has this guard, so the two layers provide
    belt-and-suspenders protection. Duplicating it at the pure-function layer
    means any caller (including tests and future consumers) gets the safety
    for free and double-passes can never double-wrap.
    """

    def test_double_pass_is_idempotent(self):
        """Calling _build_migrated_content twice returns the same content.

        Round 5, item 2: _build_migrated_content now guards on
        MANAGED_START_MARKER presence and returns unchanged content on the
        second call. The prior behavior was to double-wrap; that contract
        was intentional documentation, not a design goal.
        """
        from shared.claude_md_manager import _build_migrated_content

        original = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n"
        )
        first_pass = _build_migrated_content(original)

        # Second pass: the guard returns first_pass unchanged
        second_pass = _build_migrated_content(first_pass)

        assert second_pass.count(_MANAGED_START) == 1
        assert second_pass == first_pass

    def test_already_migrated_input_returns_unchanged(self):
        """Passing already-wrapped content returns byte-identical output."""
        from shared.claude_md_manager import _build_migrated_content

        already_managed = (
            f"{_MANAGED_START}\n"
            "# PACT Framework and Managed Project Memory\n"
            "\n"
            f"{_MEMORY_START}\n"
            "## Retrieved Context\n"
            "## Pinned Context\n"
            "## Working Memory\n"
            f"{_MEMORY_END}\n"
            f"{_MANAGED_END}\n"
        )

        result = _build_migrated_content(already_managed)

        assert result == already_managed

    def test_migrate_to_managed_structure_guards_double_call(self, tmp_path, monkeypatch):
        """The integration wrapper migrate_to_managed_structure() prevents
        double-migration via its idempotency check.
        """
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n\n## Retrieved Context\n\n## Working Memory\n"
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        first = migrate_to_managed_structure()
        assert first is not None

        content_after = claude_md.read_text()
        assert content_after.count(_MANAGED_START) == 1

        second = migrate_to_managed_structure()
        assert second is None  # no-op

        assert claude_md.read_text() == content_after  # unchanged


class TestMigrateToManagedStructure:
    """Integration tests for migrate_to_managed_structure() — the wrapper
    that does file I/O around _build_migrated_content().
    """

    def test_migrates_existing_file(self, tmp_path, monkeypatch):
        """migrate_to_managed_structure() rewrites an old-format file."""
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        result = migrate_to_managed_structure()

        assert result is not None
        assert "Migrated" in result
        content = claude_md.read_text(encoding="utf-8")
        assert _MANAGED_START in content
        assert _MANAGED_END in content
        assert _MEMORY_START in content
        assert _MEMORY_END in content

    def test_idempotent_noop_when_already_migrated(self, tmp_path, monkeypatch):
        """Second call returns None (no-op) when PACT_MANAGED_START is present."""
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        # First call migrates
        first = migrate_to_managed_structure()
        assert first is not None

        content_after_first = claude_md.read_text(encoding="utf-8")

        # Second call is no-op
        second = migrate_to_managed_structure()
        assert second is None

        # File unchanged
        assert claude_md.read_text(encoding="utf-8") == content_after_first

    def test_returns_none_when_no_project_dir(self, monkeypatch):
        """Returns None when CLAUDE_PROJECT_DIR not set."""
        from shared.claude_md_manager import migrate_to_managed_structure

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        result = migrate_to_managed_structure()

        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path, monkeypatch):
        """Returns None when file doesn't exist (new_default source)."""
        from shared.claude_md_manager import migrate_to_managed_structure

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = migrate_to_managed_structure()

        assert result is None

    def test_returns_none_when_read_fails(self, tmp_path, monkeypatch):
        """OSError on read_text -> returns None (file appears unreadable).

        Exercises the `except OSError: return None` branch at the read_text
        call inside migrate_to_managed_structure(). Uses identity-scoped
        patching so file_lock internals are not affected.
        """
        from unittest.mock import patch as mock_patch
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text("# Project Memory\n\n## Working Memory\n")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        original_read_text = Path.read_text

        def selective_read_text(self, *args, **kwargs):
            if str(self) == str(claude_md):
                raise OSError("simulated read failure")
            return original_read_text(self, *args, **kwargs)

        with mock_patch.object(Path, "read_text", selective_read_text):
            result = migrate_to_managed_structure()

        assert result is None

    def test_symlink_guard(self, tmp_path, monkeypatch):
        """Returns 'skipped' message when target is a symlink."""
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        real_file = tmp_path / "real_claude.md"
        real_file.write_text("# Project Memory\n")
        claude_md = project_dir / "CLAUDE.md"
        claude_md.symlink_to(real_file)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        result = migrate_to_managed_structure()

        assert result is not None
        assert "skipped" in result.lower()

    def test_migrated_file_has_secure_permissions(self, tmp_path, monkeypatch):
        """Migrated file should have 0o600 permissions."""
        import stat
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text("# Project Memory\n\n## Retrieved Context\n")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        migrate_to_managed_structure()

        file_mode = stat.S_IMODE(claude_md.stat().st_mode)
        assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"

    def test_timeout_returns_fail_open_status(self, tmp_path, monkeypatch):
        """Lock timeout returns a 'failed' message for session_init routing."""
        import threading
        from shared.claude_md_manager import migrate_to_managed_structure, file_lock
        from shared import claude_md_manager as cmm

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text("# Project Memory\n\n## Working Memory\n")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
        monkeypatch.setattr(cmm, "_LOCK_TIMEOUT_SECONDS", 0.3)

        holder_has_lock = threading.Event()
        holder_release = threading.Event()

        def holder():
            with file_lock(claude_md):
                holder_has_lock.set()
                holder_release.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        assert holder_has_lock.wait(timeout=2)

        result = migrate_to_managed_structure()

        assert result is not None
        assert "failed" in result.lower()
        assert "lock" in result.lower()
        # File was NOT mutated (fail-open)
        assert _MANAGED_START not in claude_md.read_text(encoding="utf-8")

        holder_release.set()
        t.join(timeout=5)

    def test_oserror_on_write_returns_failure(self, tmp_path, monkeypatch):
        """OSError during write_text returns a 'failed' message."""
        from unittest.mock import patch as mock_patch
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text("# Project Memory\n\n## Working Memory\n")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        with mock_patch.object(
            type(claude_md), "write_text", side_effect=OSError("disk full")
        ):
            result = migrate_to_managed_structure()

        assert result is not None
        assert "failed" in result.lower()

    def test_works_with_dot_claude_location(self, tmp_path, monkeypatch):
        """Migration should work for files in the .claude/ subdirectory."""
        from shared.claude_md_manager import migrate_to_managed_structure

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        dot_claude = project_dir / ".claude"
        dot_claude.mkdir()
        claude_md = dot_claude / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "\n"
            "## Pinned Context\n"
            "\n"
            "## Working Memory\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        result = migrate_to_managed_structure()

        assert result is not None
        assert "Migrated" in result
        content = claude_md.read_text(encoding="utf-8")
        assert _MANAGED_START in content


class TestEnsureProjectMemoryMdNewMarkers:
    """Verify that ensure_project_memory_md() template includes #404 markers."""

    def test_created_file_has_managed_boundary(self, tmp_path, monkeypatch):
        """Newly created project CLAUDE.md must have PACT_MANAGED markers."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        assert _MANAGED_START in content
        assert _MANAGED_END in content

    def test_created_file_has_memory_boundary(self, tmp_path, monkeypatch):
        """Newly created project CLAUDE.md must have PACT_MEMORY markers."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        assert _MEMORY_START in content
        assert _MEMORY_END in content

    def test_created_file_has_new_top_heading(self, tmp_path, monkeypatch):
        """Template heading is the single canonical H1
        '# PACT Framework and Managed Project Memory'."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        assert "# PACT Framework and Managed Project Memory" in content
        # Single-H1 invariant: only one top-level heading in the template.
        lines = content.splitlines()
        top_level_headings = [l for l in lines if l.startswith("# ") and not l.startswith("## ")]
        assert len(top_level_headings) == 1
        assert top_level_headings[0] == "# PACT Framework and Managed Project Memory"

    def test_created_file_memory_sections_inside_boundary(self, tmp_path, monkeypatch):
        """Memory sections in new file must be inside PACT_MEMORY boundary."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        mem_start_idx = content.index(_MEMORY_START)
        mem_end_idx = content.index(_MEMORY_END)
        memory_region = content[mem_start_idx:mem_end_idx]
        assert "## Retrieved Context" in memory_region
        assert "## Pinned Context" in memory_region
        assert "## Working Memory" in memory_region

    def test_created_file_marker_ordering(self, tmp_path, monkeypatch):
        """Markers in newly created file must follow the canonical order."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()

        positions = {
            "managed_start": content.index(_MANAGED_START),
            "session_start": content.index(_SESSION_START),
            "memory_start": content.index(_MEMORY_START),
            "memory_end": content.index(_MEMORY_END),
            "managed_end": content.index(_MANAGED_END),
        }

        assert positions["managed_start"] < positions["session_start"]
        assert positions["session_start"] < positions["memory_start"]
        assert positions["memory_start"] < positions["memory_end"]
        assert positions["memory_end"] < positions["managed_end"]

    def test_created_file_no_interior_h1_inside_memory_boundary(
        self, tmp_path, monkeypatch
    ):
        """With the single-H1 restructure (#404), the template must not put any
        top-level heading inside PACT_MEMORY — memory sections begin directly
        with their H2 headings.
        """
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        mem_start_idx = content.index(_MEMORY_START)
        mem_end_idx = content.index(_MEMORY_END)
        memory_region = content[mem_start_idx:mem_end_idx]
        memory_lines = memory_region.splitlines()
        h1_headings = [l for l in memory_lines if l.startswith("# ") and not l.startswith("## ")]
        assert h1_headings == [], (
            f"PACT_MEMORY region should have no H1 headings, found: {h1_headings}"
        )


class TestManagedMarkerConstants:
    """Verify the new marker constants match expected values.

    Same pattern as TestPactRoutingBlock — pin the exact values here
    so accidental drift in the implementation is caught.
    """

    def test_managed_start_marker_value(self):
        from shared.claude_md_manager import MANAGED_START_MARKER
        assert MANAGED_START_MARKER == _MANAGED_START

    def test_managed_end_marker_value(self):
        from shared.claude_md_manager import MANAGED_END_MARKER
        assert MANAGED_END_MARKER == _MANAGED_END

    def test_memory_start_marker_value(self):
        from shared.claude_md_manager import MEMORY_START_MARKER
        assert MEMORY_START_MARKER == _MEMORY_START

    def test_memory_end_marker_value(self):
        from shared.claude_md_manager import MEMORY_END_MARKER
        assert MEMORY_END_MARKER == _MEMORY_END

    def test_managed_marker_names_avoid_pact_start_collision(self):
        """Marker names use PACT_MANAGED, NOT PACT_START, to avoid collision
        with old kernel block markers that strip_orphan_kernel_block() searches for.
        """
        from shared.claude_md_manager import MANAGED_START_MARKER
        assert "PACT_MANAGED_START" in MANAGED_START_MARKER
        assert "<!-- PACT_START" not in MANAGED_START_MARKER

    def test_markers_mutually_distinct(self):
        """No marker string may be a prefix of — or a substring of — any other.

        A prefix collision would break substring search via ``str.startswith``.
        A substring collision would break ``in``-operator lookup at any
        position. For example, if ``<!-- PACT_MANAGED_START`` were embedded
        inside ``<!-- PACT_META_MANAGED_START_V2``, an ``in``-operator lookup
        for the shorter marker would spuriously match the longer one even
        though ``startswith`` would not. This is the semantic invariant —
        an explicit cardinality check (len == 4) tested the counting mistake,
        not the substring-safety contract.

        Both relations are checked (``startswith`` AND ``in``) so future
        marker additions with embedded patterns are caught.
        """
        from shared import claude_md_manager as cmm
        markers = [
            cmm.MANAGED_START_MARKER,
            cmm.MANAGED_END_MARKER,
            cmm.MEMORY_START_MARKER,
            cmm.MEMORY_END_MARKER,
        ]
        for i, a in enumerate(markers):
            for j, b in enumerate(markers):
                if i == j:
                    continue
                assert not a.startswith(b), (
                    f"Marker prefix collision: {a!r} starts with {b!r}"
                )
                assert b not in a, (
                    f"Marker substring collision: {b!r} is contained in {a!r}"
                )

    def test_managed_title_symbol_matches_literal(self):
        """Round 6 item 5: symbol-level drift guard for ``MANAGED_TITLE``.

        The round 5 refactor extracted ``MANAGED_TITLE`` as a module
        constant so the three template sites (``ensure_project_memory_md``,
        ``_build_migrated_content``, and ``session_resume.update_session_info``
        Case 0) could not drift apart. This test pins the literal value so
        an accidental rename (e.g., "# PACT Framework" typo'd to
        "# PACT Framework" with an extra space) is caught by a targeted
        assertion rather than via indirect template-shape test failures.
        """
        from shared.claude_md_manager import MANAGED_TITLE
        assert MANAGED_TITLE == "# PACT Framework and Managed Project Memory"

    def test_managed_title_no_literal_copies_in_claude_md_manager(self):
        """PR #404: ensure the ``MANAGED_TITLE`` literal appears in
        ``claude_md_manager.py`` exactly 2 times (1 constant definition +
        1 docstring mention) — not hand-copied into a template string.

        This is a source-scan drift guard. The literal is allowed inside
        docstring examples and code comments (because those are
        documentation, not code that would drift), so the check counts
        assignment-style RHS occurrences and tolerates docstring mentions.
        The simple shape: the literal must appear no more than ``N+1``
        times in the source file where ``N`` is the allowed docstring /
        comment mentions (currently 1 — the migration strategy docstring).

        If a future refactor intentionally inlines the literal in a new
        comment/docstring, bump the allowed count here rather than
        allowing silent drift at a code site.

        Scope (round 7 item 4): this guard catches **copy-paste drift**
        of the literal title string across source files. It does NOT
        catch string fragmentation — e.g., a developer writing
        ``"# PACT " + "Framework and Managed Project Memory"`` or an
        f-string like ``f"# PACT Framework and {suffix}"``. That class
        of evasion is **out of scope** because the guard targets
        accidental drift (the common failure mode), not adversarial
        evasion. Strengthening to catch fragmentation would require AST
        parsing, which is disproportionate — a developer who
        deliberately hard-codes the title via concatenation is already
        breaking the single-source-of-truth pattern regardless of how
        they spell it, and the resulting bug surfaces through downstream
        tests that depend on ``MANAGED_TITLE`` consistency.
        """
        source_path = (
            Path(__file__).parent.parent
            / "hooks"
            / "shared"
            / "claude_md_manager.py"
        )
        source = source_path.read_text(encoding="utf-8")
        literal = "# PACT Framework and Managed Project Memory"
        # Allowed occurrences:
        #   - 1x MANAGED_TITLE constant definition (code)
        #   - 1x migration-strategy docstring mention in
        #     ``migrate_to_managed_structure``
        # Any additional copy means someone has inlined the literal at a
        # code site instead of referencing ``MANAGED_TITLE``, which is the
        # drift pattern item 5 guards against.
        count = source.count(literal)
        assert count == 2, (
            f"Expected exactly 2 occurrences of MANAGED_TITLE literal in "
            f"claude_md_manager.py (1 constant def + 1 docstring mention), "
            f"found {count}. A hand-copied literal indicates drift — use "
            f"the MANAGED_TITLE symbol at code sites instead."
        )

    def test_managed_title_no_literal_copies_in_session_resume(self):
        """PR #404: drift guard for ``session_resume.py``.

        ``update_session_info`` Case 0 (the fresh-file creation path)
        builds a PACT_MANAGED block and must use the imported
        ``MANAGED_TITLE`` symbol, not a hand-copied literal. One comment
        mention is tolerated; any additional occurrence indicates code-site
        drift.

        Scope (round 7 item 4): same bounds as the sibling guard on
        ``claude_md_manager.py`` — catches **copy-paste drift** of the
        literal title string, does NOT catch string fragmentation
        (concatenation, f-string interpolation). That is out of scope
        because the guard targets accidental drift, not adversarial
        evasion; catching fragmentation would require AST parsing and
        is disproportionate to the threat model.
        """
        source_path = (
            Path(__file__).parent.parent
            / "hooks"
            / "shared"
            / "session_resume.py"
        )
        source = source_path.read_text(encoding="utf-8")
        literal = "# PACT Framework and Managed Project Memory"
        # Allowed occurrences:
        #   - 1x docstring/comment mention documenting the Case 0 template
        # No code-site copy is permitted; ``MANAGED_TITLE`` is imported at
        # the top of the file and used as a symbol at the single site.
        count = source.count(literal)
        assert count == 1, (
            f"Expected exactly 1 occurrence of MANAGED_TITLE literal in "
            f"session_resume.py (comment mention only), found {count}. "
            f"Use the imported MANAGED_TITLE symbol at code sites."
        )


class TestSessionInitMigrationIntegration:
    """Verify that session_init.py calls migrate_to_managed_structure()
    and routes its return value correctly.
    """

    def test_session_init_calls_migration(self):
        """session_init.py must contain a call to migrate_to_managed_structure."""
        session_init_path = (
            Path(__file__).parent.parent / "hooks" / "session_init.py"
        )
        source = session_init_path.read_text(encoding="utf-8")
        assert "migrate_to_managed_structure()" in source

    def test_migration_result_routing_failed(self):
        """session_init routes 'failed'/'skipped' migration messages to
        system_messages, not context_parts.
        """
        session_init_path = (
            Path(__file__).parent.parent / "hooks" / "session_init.py"
        )
        source = session_init_path.read_text(encoding="utf-8")
        # The routing logic checks for "failed" or "skipped" in the message
        assert '"failed"' in source or "'failed'" in source
        assert '"skipped"' in source or "'skipped'" in source


class TestStripLegacyLines:
    """Direct unit tests for `_strip_legacy_lines`.

    Direct coverage so a regression in the legacy-stripping logic
    produces a targeted failure rather than masked signal from the
    indirect call sites in `_build_migrated_content` and the every-
    session pass inside `strip_orphan_routing_markers`.
    """

    # The exact stale line the v3.16.2 template carried. Pinned here as a
    # fixture constant so drift in _STALE_ORCHESTRATOR_LINE_RE is caught.
    STALE_LINE = (
        "The global PACT Orchestrator is loaded from `~/.claude/CLAUDE.md`."
    )

    def test_strips_exact_stale_line_with_trailing_period(self):
        """Canonical form: stale line with trailing period and newline."""
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            f"{self.STALE_LINE}\n"
            "\n"
            "## Retrieved Context\n"
        )

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE not in result
        # Surrounding content survives
        assert "# Project Memory" in result
        assert "## Retrieved Context" in result

    def test_strips_stale_line_without_trailing_period(self):
        """Regex uses `\\.?` to match the line with OR without a trailing
        period — the v3.16.2 template has the period; hand-edited copies
        may lack it.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        # No trailing period after `CLAUDE.md`
        no_period = "The global PACT Orchestrator is loaded from `~/.claude/CLAUDE.md`"
        content = (
            "# Project Memory\n"
            f"{no_period}\n"
            "## Retrieved Context\n"
        )

        result = _strip_legacy_lines(content)

        assert no_period not in result

    def test_absent_stale_line_returns_content_unchanged(self):
        """No-op case: when the stale line is absent, the helper returns
        content identical to the input. Idempotency guarantee.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "## Retrieved Context\n"
            "Some retrieved data.\n"
            "\n"
            "## Working Memory\n"
        )

        result = _strip_legacy_lines(content)

        assert result == content, (
            "_strip_legacy_lines must be a no-op when no stale line is present"
        )

    def test_idempotent_across_two_invocations(self):
        """Applying `_strip_legacy_lines` twice is the same as applying it
        once — the function is pure and deterministic. This matches the
        expectation of shared helper usage (`_build_migrated_content` and
        the every-session pass inside `strip_orphan_routing_markers` both
        call it; running both consecutively must not corrupt content).
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            f"{self.STALE_LINE}\n"
            "## Pinned Context\n"
            "### Pin\n"
            "Body.\n"
        )

        once = _strip_legacy_lines(content)
        twice = _strip_legacy_lines(once)

        assert once == twice
        assert self.STALE_LINE not in once

    def test_preserves_other_content_mentioning_orchestrator(self):
        """A line that mentions the word "orchestrator" but is not the
        exact stale template line must NOT be stripped. The regex is
        anchored to the full stale-line text.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "The orchestrator loads from somewhere else entirely.\n"
            "See also: orchestrator governance.\n"
            "\n"
            "## Pinned Context\n"
        )

        result = _strip_legacy_lines(content)

        # Both lines mention "orchestrator" but don't match the stale pattern
        assert "The orchestrator loads from somewhere else entirely." in result
        assert "See also: orchestrator governance." in result

    # Round 8 item 5: direct fence unit tests for `_strip_legacy_lines`.
    # Prior coverage exercised the walker's fence branches through
    # `_build_migrated_content` and `test_fenced_stale_orchestrator_line_preserved`
    # (an end-to-end driver). Direct unit tests produce targeted failures when
    # the walker's fence-state tracking regresses, without the downstream
    # migration-pipeline assertions masking the signal.

    def test_strip_legacy_lines_backtick_fenced_stale_line_preserved(self):
        """A stale line INSIDE a backtick fence must be preserved byte-for-byte.

        Round 7 item 2 added fence-awareness via `in_code_fence`; this unit
        test pins that behavior so a regression in the backtick-fence branch
        (distinct from the tilde branch below) fails here instead of only
        failing via the end-to-end driver.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "```\n"
            f"{self.STALE_LINE}\n"
            "```\n"
        )

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE in result, (
            "Stale line inside backtick fence must be preserved verbatim"
        )
        # The fence boundaries also survive intact.
        assert result.count("```") == 2

    def test_strip_legacy_lines_tilde_fenced_stale_line_preserved(self):
        """Round 8 item 1: tilde fences (CommonMark §4.5) must be recognized.

        Pre-round-8 `_strip_legacy_lines` only recognized backtick (```)
        fences. A user-authored `~~~` fence containing the stale line was
        treated as non-fenced content and silently destroyed. Round 8 adds
        an independent `in_tilde_fence` state so the stripper skips
        tilde-fenced content the same way it skips backtick-fenced content.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "~~~\n"
            f"{self.STALE_LINE}\n"
            "~~~\n"
        )

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE in result, (
            "Stale line inside tilde fence must be preserved verbatim "
            "(round 8 item 1)"
        )
        assert result.count("~~~") == 2

    def test_strip_legacy_lines_unclosed_fence_at_eof_preserves_content(self):
        """An unclosed fence at EOF must still protect the content below.

        When a user's content has an opening fence with no matching close
        (file ends before the fence is closed), the walker should remain
        in-fence through to the end of the content. Any stale-line match
        inside the unclosed fence must be preserved.

        This is CommonMark-compatible: §4.5 explicitly allows unclosed
        fenced code blocks to extend to the end of the document.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "```\n"
            f"{self.STALE_LINE}\n"
            "more content that never gets un-fenced\n"
        )

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE in result, (
            "Stale line inside unclosed fence must be preserved — the "
            "walker's in-fence state must not reset at EOF"
        )
        assert "more content that never gets un-fenced" in result

    def test_strip_legacy_lines_indented_fence_preserves_content(self):
        """A fence with leading whitespace (`    \\`\\`\\``) is still detected.

        The walker uses `stripped = line.lstrip()` before checking for
        ```/~~~ prefixes, so an indented fence opener is recognized. The
        pattern matches Markdown conventions where a fence inside a list
        item or blockquote may be indented.

        Note: CommonMark §4.5 technically requires closing fences to have
        the same or less indentation than the opener, and treats leading
        whitespace >3 spaces as indicating an indented code block instead
        of a fenced block. Our walker uses a simpler "any leading
        whitespace" convention for symmetry with the other walker sites
        (_find_preamble_cutoff, staleness._find_terminator_offset,
        working_memory._find_terminator_offset) — this is documented
        divergence from strict CommonMark, sufficient for CLAUDE.md use.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "- List item with fenced example:\n"
            "  ```\n"
            f"  {self.STALE_LINE}\n"
            "  ```\n"
        )

        result = _strip_legacy_lines(content)

        # The stale line must survive — it's inside an indented fence body.
        assert self.STALE_LINE in result, (
            "Stale line inside indented fence must be preserved — the "
            "walker's `stripped = line.lstrip()` normalization must "
            "recognize leading-whitespace fence openers"
        )

    def test_strip_legacy_lines_consecutive_fences_state_resets(self):
        """Two consecutive fences: content inside BOTH must survive.

        The walker toggles fence state on each fence line, so after a
        fence closes, the next fence opener should correctly re-enter
        the in-fence state. This test pins the toggle behavior — a
        regression that fails to reset state (e.g., a sticky in-fence
        flag) would strip the stale line in the second fence.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "```\n"
            f"{self.STALE_LINE}\n"
            "```\n"
            "\n"
            "Some non-fenced narrative.\n"
            "\n"
            "```\n"
            f"{self.STALE_LINE}\n"
            "```\n"
        )

        result = _strip_legacy_lines(content)

        # Both occurrences of the stale line (inside each fence) must
        # survive. The non-fenced narrative line in between is not a
        # stale-line match, so it also survives.
        assert result.count(self.STALE_LINE) == 2, (
            "Both fenced stale lines must survive — state must reset "
            "correctly between consecutive fences"
        )
        assert "Some non-fenced narrative." in result

    def test_strip_legacy_lines_backtick_inside_tilde_fence_is_inert(self):
        """Independent-state invariant: a ``` line INSIDE a ~~~ fence
        must NOT toggle backtick state.

        This test pins the CommonMark §4.5 guarantee that fence
        delimiters of different characters are independent. Without the
        independent-state tracking, a user's tilde-fenced example that
        shows a backtick-fence snippet inside it would see the backtick
        "line" treated as a fence boundary, flip the backtick state, and
        fool the walker into exiting in-fence state early. The stale
        line that follows would then be stripped.

        Pairs with `test_strip_legacy_lines_tilde_fenced_stale_line_preserved`
        which exercises the simple tilde-only case; this test exercises
        the nested / interaction case.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Project Memory\n"
            "\n"
            "~~~\n"
            "Example: how to open a code fence in Markdown:\n"
            "```\n"  # This ``` line is INSIDE a ~~~ fence, must be inert
            f"{self.STALE_LINE}\n"
            "```\n"  # Still inside ~~~, still inert
            "More content inside the tilde fence.\n"
            "~~~\n"
        )

        result = _strip_legacy_lines(content)

        # The stale line must survive — it's inside a ~~~ fence, and
        # the nested ``` lines do not toggle backtick state.
        assert self.STALE_LINE in result, (
            "Stale line inside backtick-inside-tilde nested fence must "
            "be preserved — backtick and tilde fence states must be "
            "independent (CommonMark §4.5)"
        )
        # All content lines also survive
        assert "Example: how to open a code fence in Markdown:" in result
        assert "More content inside the tilde fence." in result

    def test_strip_legacy_lines_length_tracked_fence_state(self):
        """Round 10 item 9: CommonMark §4.5 variable-length fence support.

        A 4-backtick outer fence (````) containing a 3-backtick inner
        example (```) must NOT close the outer fence on the inner line.
        Pre-round-10 behavior used boolean toggles which would falsely
        close the fence on the 3-backtick line, exposing the remainder
        of the outer fence body to legacy-line stripping.

        Counter-test-by-revert: revert `_strip_legacy_lines` to boolean
        toggles -> this test MUST fail; restore length-tracked state ->
        this test MUST pass.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Notes\n"
            "\n"
            "````markdown\n"
            "Here is an inner example:\n"
            "```\n"
            f"{self.STALE_LINE}\n"
            "```\n"
            "Still inside the 4-backtick outer fence.\n"
            "````\n"
        )

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE in result, (
            "Stale line inside 4-backtick outer fence (with 3-backtick "
            "inner example) must be preserved — length-tracked fence "
            "state required (round 10 item 9)"
        )
        assert "Still inside the 4-backtick outer fence." in result
        assert "Here is an inner example:" in result

    def test_strip_legacy_lines_length_tracked_tilde_fence(self):
        """Same as above but with tilde fences: 4-tilde outer, 3-tilde inner."""
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Notes\n"
            "\n"
            "~~~~\n"
            "Inner tilde example:\n"
            "~~~\n"
            f"{self.STALE_LINE}\n"
            "~~~\n"
            "Still inside the 4-tilde outer fence.\n"
            "~~~~\n"
        )

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE in result, (
            "Stale line inside 4-tilde outer fence must be preserved"
        )
        assert "Still inside the 4-tilde outer fence." in result

    def test_strip_legacy_lines_closing_fence_needs_no_info_string(self):
        """CommonMark §4.5: closing fence cannot have an info string.
        A line with ``` followed by non-whitespace is NOT a closing fence.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        content = (
            "# Notes\n"
            "\n"
            "```\n"
            "```python\n"  # NOT a closing fence (has info string)
            f"{self.STALE_LINE}\n"
            "```\n"  # This IS the real close
        )

        result = _strip_legacy_lines(content)

        # The stale line is inside the fence (```python is not a close)
        assert self.STALE_LINE in result

    def test_strip_legacy_lines_no_trailing_newline(self):
        """PR #404 round 12 item 6: content with no trailing newline
        must still strip the stale line on the final line.

        Covers lines 217-219: the ``nl == -1`` branch where the last
        line in content has no ``\\n`` terminator.
        """
        from shared.claude_md_manager import _strip_legacy_lines

        # Stale line is the last line, no trailing newline
        content = f"# Notes\n{self.STALE_LINE}"

        result = _strip_legacy_lines(content)

        assert self.STALE_LINE not in result
        assert "# Notes" in result


class TestEnsureProjectMemoryMdOSErrorOnMkdir:
    """PR #404 round 12 item 6: OSError during .claude/ directory creation
    in ``ensure_project_memory_md``.

    Covers lines 823-824: the ``except OSError`` branch where
    ``target_dir.mkdir`` fails (e.g., read-only filesystem).
    """

    def test_oserror_during_mkdir_returns_failure_message(self, tmp_path, monkeypatch):
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Make the .claude directory creation fail
        dot_claude = tmp_path / ".claude"
        # Create a file at .claude to block mkdir
        dot_claude.write_text("blocker")

        result = ensure_project_memory_md()

        assert result is not None
        assert "failed" in result.lower() or "skipped" in result.lower()


class TestExtractManagedRegion:
    """Round 10: tests for extract_managed_region helper."""

    def test_returns_region_and_offset(self):
        """When both markers are present, returns (region_text, offset)."""
        from shared.claude_md_manager import (
            MANAGED_START_MARKER,
            MANAGED_END_MARKER,
            extract_managed_region,
        )

        content = (
            "user preamble\n"
            f"{MANAGED_START_MARKER}\n"
            "managed content here\n"
            f"{MANAGED_END_MARKER}\n"
            "user epilogue\n"
        )

        result = extract_managed_region(content)
        assert result is not None
        region_text, offset = result
        assert "managed content here" in region_text
        assert "user preamble" not in region_text
        assert "user epilogue" not in region_text
        # offset should point to just after MANAGED_START_MARKER
        assert content[offset:].startswith("\nmanaged")

    def test_returns_none_when_start_missing(self):
        """When MANAGED_START_MARKER is absent, returns None."""
        from shared.claude_md_manager import (
            MANAGED_END_MARKER,
            extract_managed_region,
        )

        content = f"some content\n{MANAGED_END_MARKER}\n"
        assert extract_managed_region(content) is None

    def test_returns_none_when_end_missing(self):
        """When MANAGED_END_MARKER is absent, returns None."""
        from shared.claude_md_manager import (
            MANAGED_START_MARKER,
            extract_managed_region,
        )

        content = f"{MANAGED_START_MARKER}\nsome content\n"
        assert extract_managed_region(content) is None

    def test_returns_none_for_empty_string(self):
        """Empty string has no markers."""
        from shared.claude_md_manager import extract_managed_region

        assert extract_managed_region("") is None

    def test_offset_enables_correct_writeback(self):
        """The offset should allow callers to map managed-region positions
        back to full-content positions for write-back operations.
        """
        from shared.claude_md_manager import (
            MANAGED_START_MARKER,
            MANAGED_END_MARKER,
            extract_managed_region,
        )

        preamble = "user notes above\n\n"
        managed_body = "## Pinned Context\npin content\n"
        epilogue = "\nuser notes below\n"

        content = (
            preamble
            + MANAGED_START_MARKER + "\n"
            + managed_body
            + MANAGED_END_MARKER + "\n"
            + epilogue
        )

        result = extract_managed_region(content)
        assert result is not None
        region_text, offset = result

        # Find "pin content" in the region
        local_idx = region_text.find("pin content")
        assert local_idx >= 0

        # Map back to full content
        full_idx = local_idx + offset
        assert content[full_idx:full_idx + len("pin content")] == "pin content"



class _StripOrphanBlockTestBase:
    """Shared mixin for SUNSET-BEFORE-v4.x.y orphan-block strippers.

    Subclasses configure:
      - START_MARKER, END_MARKER (the marker pair the stripper hunts)
      - target_file fixture (Path to the file the stripper operates on)
      - call_stripper(self) -> str | None (invokes the stripper)

    Each subclass exercises the same behavior matrix:
      - both markers present + properly ordered → strip + return success status
      - neither marker present → no-op (None)
      - only START present → defensive no-op + skip status
      - only END present → defensive no-op + skip status
      - END before START → defensive no-op + skip status
      - symlink → defensive no-op + skip status
      - read OSError → fail-open (None)

    Concrete classes follow at module bottom: TestStripOrphanKernelBlock
    (home-dir kernel block) and TestStripOrphanRoutingMarkers
    (project-dir routing block).
    """

    START_MARKER: str
    END_MARKER: str

    def _write_target(self, target_file: Path, content: str) -> None:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(content, encoding="utf-8")

    def call_stripper(self) -> str | None:
        raise NotImplementedError

    def get_target_file(self) -> Path:
        raise NotImplementedError


class TestStripOrphanKernelBlock(_StripOrphanBlockTestBase):
    """SUNSET-BEFORE-v4.x.y migration helper: strips the obsolete v3.x
    PACT_START/PACT_END block from ~/.claude/CLAUDE.md.

    Uses the _StripOrphanBlockTestBase mixin to share the behavior matrix
    with TestStripOrphanRoutingMarkers (project-dir variant).
    """

    START_MARKER = "<!-- PACT_START:v3.16 -->"
    END_MARKER = "<!-- PACT_END -->"

    @pytest.fixture(autouse=True)
    def _setup(self, mock_home):
        self.target_file = mock_home / ".claude" / "CLAUDE.md"

    def call_stripper(self) -> str | None:
        from shared.claude_md_manager import strip_orphan_kernel_block
        return strip_orphan_kernel_block()

    def test_proper_pair_strips_block_and_returns_success(self):
        self._write_target(
            self.target_file,
            f"# user content\n{self.START_MARKER}\nstale prose\n{self.END_MARKER}\nmore user content\n",
        )
        result = self.call_stripper()
        assert result is not None
        assert "Removed obsolete PACT kernel block" in result
        new_content = self.target_file.read_text(encoding="utf-8")
        assert self.START_MARKER not in new_content
        assert self.END_MARKER not in new_content
        assert "user content" in new_content
        assert "more user content" in new_content
        assert "stale prose" not in new_content

    def test_no_markers_returns_none(self):
        self._write_target(self.target_file, "# user content only\n")
        assert self.call_stripper() is None

    def test_orphan_start_only_returns_skip_status(self):
        self._write_target(self.target_file, f"# pre\n{self.START_MARKER}\n# post\n")
        result = self.call_stripper()
        assert result is not None
        assert "skipped" in result.lower()
        assert "PACT_END" in result
        # File must NOT be rewritten when defensive-skipping
        assert self.START_MARKER in self.target_file.read_text(encoding="utf-8")

    def test_orphan_end_only_returns_skip_status(self):
        self._write_target(self.target_file, f"# pre\n{self.END_MARKER}\n# post\n")
        result = self.call_stripper()
        assert result is not None
        assert "skipped" in result.lower()
        assert "PACT_START" in result
        assert self.END_MARKER in self.target_file.read_text(encoding="utf-8")

    def test_reversed_pair_returns_skip_status(self):
        self._write_target(
            self.target_file,
            f"# pre\n{self.END_MARKER}\nbody\n{self.START_MARKER}\n# post\n",
        )
        result = self.call_stripper()
        assert result is not None
        assert "skipped" in result.lower()
        assert "PACT_END appears before PACT_START" in result

    def test_symlink_returns_skip_status(self, tmp_path):
        # Replace target_file with a symlink to a real file under tmp_path
        real_target = tmp_path / "real_claude.md"
        real_target.write_text(
            f"# user\n{self.START_MARKER}\nstale\n{self.END_MARKER}\n",
            encoding="utf-8",
        )
        self.target_file.parent.mkdir(parents=True, exist_ok=True)
        if self.target_file.exists():
            self.target_file.unlink()
        self.target_file.symlink_to(real_target)
        result = self.call_stripper()
        assert result is not None
        assert "skipped" in result.lower()
        # Real target must not have been rewritten
        assert self.START_MARKER in real_target.read_text(encoding="utf-8")

    def test_missing_target_file_returns_none(self):
        # File doesn't exist — stripper short-circuits to None.
        if self.target_file.exists():
            self.target_file.unlink()
        assert self.call_stripper() is None
