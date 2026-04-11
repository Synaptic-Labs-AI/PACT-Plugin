"""
Tests for shared/claude_md_manager.py -- CLAUDE.md file manipulation
post #366 Phase 1 kernel elimination refactor.

Tests cover:

remove_stale_kernel_block() — one-time migration that strips the obsolete
PACT_START/PACT_END block from ~/.claude/CLAUDE.md left over from PR #390:
1. Block present + valid → removed, user content preserved
2. Block absent → no-op, returns None
3. Block malformed (PACT_START with no PACT_END) → defensive no-op
4. Home file missing → returns None

update_pact_routing() — idempotent project CLAUDE.md routing block management:
5. Markers present + already canonical → no write, returns None
6. Markers present + stale content → content replaced, surrounding content preserved
7. Markers absent → block inserted near top of file, pre-existing content preserved
8. File doesn't exist (new_default source) → returns None (deferred to ensure_project_memory_md)

ensure_project_memory_md() — project CLAUDE.md creation:
9. Returns None when CLAUDE_PROJECT_DIR not set
10. Returns None when project CLAUDE.md already exists (legacy ./CLAUDE.md)
11. Creates project CLAUDE.md (.claude/CLAUDE.md, new default) with memory sections
12. Created file contains session markers
13. Created file contains the canonical _PACT_ROUTING_BLOCK verbatim
14. Returns None when .claude/CLAUDE.md already exists (no overwrite)
15. Returns None when only legacy ./CLAUDE.md exists (no migration)
16. .claude/CLAUDE.md takes precedence when both locations exist
17. Created .claude/CLAUDE.md has 0o600 permissions; .claude/ dir 0o700

_PACT_ROUTING_BLOCK constant — load-bearing fixture:
18. Constant matches the canonical 18-line text byte-for-byte
19. Constant has no leading or trailing newlines (Python string precision)
"""

import os
import sys
from pathlib import Path

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# ---------------------------------------------------------------------------
# Canonical fixture: the exact 18-line PACT routing block
# ---------------------------------------------------------------------------
# This is the byte-exact content the implementation must match. Pinned here
# in the test file so any accidental drift in claude_md_manager.py is caught.
# Includes em dash (U+2014) on line 5 and rightwards arrows (U+2192) on the
# bullet items, per Section 6.13 Format Invariants.

CANONICAL_PACT_ROUTING_BLOCK = (
    "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
    "## PACT Routing\n"
    "\n"
    "Before any other work, determine your PACT role and invoke the appropriate\n"
    "bootstrap skill. Do not skip \u2014 this loads your operating instructions,\n"
    "governance policy, and protocol references.\n"
    "\n"
    "Check your context for a `PACT ROLE:` marker:\n"
    "- `PACT ROLE: orchestrator` \u2192 invoke `Skill(\"PACT:bootstrap\")` unless already loaded.\n"
    "- `PACT ROLE: teammate (...)` \u2192 invoke `Skill(\"PACT:teammate-bootstrap\")` unless already loaded.\n"
    "\n"
    "No marker present? Inspect your system prompt: a `# Custom Agent Instructions`\n"
    "block naming a specific PACT agent means you are a teammate (invoke the\n"
    "teammate bootstrap); otherwise you are the main session (invoke the\n"
    "orchestrator bootstrap).\n"
    "\n"
    "Re-invoke after compaction if the bootstrap content is no longer present.\n"
    "<!-- PACT_ROUTING_END -->"
)


# ---------------------------------------------------------------------------
# Shared fixture: mock Path.home() so tests never touch real ~/.claude
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_home(tmp_path, monkeypatch):
    """Patch Path.home() to return a tempdir-backed ~/.claude.

    Required for any test that exercises remove_stale_kernel_block() or
    other functions that read/write under Path.home() / ".claude".
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


# ---------------------------------------------------------------------------
# _PACT_ROUTING_BLOCK constant — load-bearing fixture
# ---------------------------------------------------------------------------

class TestPactRoutingBlock:
    """Byte-exact assertions on the _PACT_ROUTING_BLOCK constant.

    The constant is load-bearing: agents read it from project CLAUDE.md to
    decide which bootstrap skill to invoke. Any drift breaks role detection.
    """

    def test_constant_matches_canonical_text(self):
        """The shared constant must match the canonical text byte-for-byte."""
        from shared.claude_md_manager import _PACT_ROUTING_BLOCK

        assert _PACT_ROUTING_BLOCK == CANONICAL_PACT_ROUTING_BLOCK

    def test_constant_has_no_leading_newline(self):
        """The constant must not start with a newline (insertion logic depends on it)."""
        from shared.claude_md_manager import _PACT_ROUTING_BLOCK

        assert not _PACT_ROUTING_BLOCK.startswith("\n")
        assert _PACT_ROUTING_BLOCK.startswith("<!-- PACT_ROUTING_START:")

    def test_constant_has_no_trailing_newline(self):
        """The constant must not end with a newline (insertion logic depends on it)."""
        from shared.claude_md_manager import _PACT_ROUTING_BLOCK

        assert not _PACT_ROUTING_BLOCK.endswith("\n")
        assert _PACT_ROUTING_BLOCK.endswith("<!-- PACT_ROUTING_END -->")

    def test_constant_contains_em_dash(self):
        """Line 5 must contain U+2014 em dash, not ASCII --."""
        from shared.claude_md_manager import _PACT_ROUTING_BLOCK

        assert "\u2014" in _PACT_ROUTING_BLOCK
        assert "Do not skip \u2014" in _PACT_ROUTING_BLOCK

    def test_constant_contains_rightwards_arrows(self):
        """Bullet items must use U+2192, not ASCII ->."""
        from shared.claude_md_manager import _PACT_ROUTING_BLOCK

        # Two bullet rows; both use U+2192
        assert _PACT_ROUTING_BLOCK.count("\u2192") == 2
        assert "orchestrator` \u2192" in _PACT_ROUTING_BLOCK
        assert "teammate (...)` \u2192" in _PACT_ROUTING_BLOCK


# ---------------------------------------------------------------------------
# remove_stale_kernel_block() — one-time migration
# ---------------------------------------------------------------------------

class TestRemoveStaleKernelBlockPresent:
    """The legacy PACT_START/PACT_END block exists and must be removed."""

    def test_strips_block_and_preserves_user_content(self, mock_home):
        """Block is removed; user content before/after survives verbatim."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "User preamble line\n"
            "More user content\n"
            "<!-- PACT_START: Managed by pact-plugin - Do not edit this block -->\n"
            "# PACT Orchestrator\n"
            "Old kernel body that must be removed\n"
            "<!-- PACT_END -->\n"
            "User trailing content\n"
            "Even more user content\n",
            encoding="utf-8",
        )

        result = remove_stale_kernel_block()

        assert result == "Removed obsolete PACT kernel block from ~/.claude/CLAUDE.md"
        new_content = target.read_text(encoding="utf-8")
        # Markers and body are gone
        assert "PACT_START" not in new_content
        assert "PACT_END" not in new_content
        assert "Old kernel body that must be removed" not in new_content
        assert "# PACT Orchestrator" not in new_content
        # User content survives verbatim
        assert "User preamble line" in new_content
        assert "More user content" in new_content
        assert "User trailing content" in new_content
        assert "Even more user content" in new_content

    def test_secure_permissions_after_write(self, mock_home):
        """Rewritten file must end up at 0o600."""
        import stat
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "before\n"
            "<!-- PACT_START: pact -->\n"
            "kernel body\n"
            "<!-- PACT_END -->\n"
            "after\n",
            encoding="utf-8",
        )

        remove_stale_kernel_block()

        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


class TestRemoveStaleKernelBlockAbsent:
    """No legacy block present — function must be a clean no-op."""

    def test_returns_none_when_home_file_missing(self, mock_home):
        """No CLAUDE.md at ~/.claude/CLAUDE.md → None, no side effects."""
        from shared.claude_md_manager import remove_stale_kernel_block

        # mock_home creates ~/.claude but not CLAUDE.md
        target = mock_home / ".claude" / "CLAUDE.md"
        assert not target.exists()

        result = remove_stale_kernel_block()

        assert result is None
        assert not target.exists()

    def test_returns_none_when_no_markers(self, mock_home):
        """File exists but contains no PACT_START → None, content unchanged."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        original = "User-managed CLAUDE.md\nNo PACT markers present\n"
        target.write_text(original, encoding="utf-8")

        result = remove_stale_kernel_block()

        assert result is None
        assert target.read_text(encoding="utf-8") == original

    def test_returns_none_when_only_end_marker(self, mock_home):
        """PACT_END alone (no START) → None, no change."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        original = "user content\n<!-- PACT_END -->\nmore content\n"
        target.write_text(original, encoding="utf-8")

        result = remove_stale_kernel_block()

        assert result is None
        assert target.read_text(encoding="utf-8") == original


class TestRemoveStaleKernelBlockMalformed:
    """PACT_START present but no PACT_END — defensive no-op to avoid data loss."""

    def test_returns_none_when_start_without_end(self, mock_home):
        """Unterminated PACT block → defensive no-op (do not corrupt)."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        original = (
            "before\n"
            "<!-- PACT_START: Managed by pact-plugin -->\n"
            "kernel body that never closes\n"
            "more content\n"
        )
        target.write_text(original, encoding="utf-8")

        result = remove_stale_kernel_block()

        # The current implementation early-returns when END is absent in the
        # full content; the file remains untouched.
        assert result is None
        assert target.read_text(encoding="utf-8") == original

    def test_returns_none_when_end_appears_before_start(self, mock_home):
        """END marker appears textually before START marker → defensive."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        original = (
            "before\n"
            "<!-- PACT_END -->\n"
            "stray END marker out of order\n"
            "<!-- PACT_START: Managed by pact-plugin -->\n"
            "kernel body that never closes after this START\n"
        )
        target.write_text(original, encoding="utf-8")

        result = remove_stale_kernel_block()

        # The function splits on START first then checks for END in the
        # remainder. Here END is before START so the remainder has no END
        # → defensive no-op.
        assert result is None
        assert target.read_text(encoding="utf-8") == original


class TestRemoveStaleKernelBlockOSError:
    """OSError on read or write paths → graceful failure, status string returned.

    Cycle 2 minor item 5/6: exercises the try/except OSError fallback
    branches in remove_stale_kernel_block. Previously these branches were
    unexercised, so a bug in the error handling (wrong format string,
    wrong truncation length, accidentally raising instead of returning)
    would not be caught by CI.
    """

    def test_returns_none_when_read_fails(self, mock_home):
        """OSError on read_text → returns None (file appears unreadable)."""
        from unittest.mock import patch
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text("placeholder content", encoding="utf-8")

        # Patch Path.read_text to raise OSError when called on the home file
        original_read_text = Path.read_text

        def selective_read_text(self, *args, **kwargs):
            if str(self) == str(target):
                raise OSError("simulated read failure")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", selective_read_text):
            result = remove_stale_kernel_block()

        assert result is None

    def test_returns_failure_status_when_write_fails(self, mock_home):
        """OSError on write_text → returns 'Failed to remove stale kernel block: ...'.

        The block is well-formed (so the function reaches the write path),
        but the write itself fails. The function must return a status
        string indicating the failure mode rather than raising.
        """
        from unittest.mock import patch
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        original = (
            "user content before\n"
            "<!-- PACT_START: Managed by pact-plugin - do not edit -->\n"
            "kernel body to be stripped\n"
            "<!-- PACT_END -->\n"
            "user content after\n"
        )
        target.write_text(original, encoding="utf-8")

        # Patch Path.write_text to raise OSError when called on the home file
        original_write_text = Path.write_text

        def selective_write_text(self, *args, **kwargs):
            if str(self) == str(target):
                raise OSError("simulated write failure")
            return original_write_text(self, *args, **kwargs)

        with patch.object(Path, "write_text", selective_write_text):
            result = remove_stale_kernel_block()

        assert result is not None
        assert "Failed to remove stale kernel block" in result
        # Original file unchanged because write was blocked
        assert target.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# update_pact_routing() — idempotent project CLAUDE.md routing block management
# ---------------------------------------------------------------------------

class TestUpdatePactRoutingIdempotent:
    """File already has the canonical block — no rewrite, returns None."""

    def test_no_write_when_block_canonical(self, tmp_path, monkeypatch):
        """Canonical content between markers → return None, no write."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        original = (
            "# Project Memory\n"
            "\n"
            f"{CANONICAL_PACT_ROUTING_BLOCK}\n"
            "\n"
            "## Working Memory\n"
        )
        legacy.write_text(original, encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result is None
        # File untouched
        assert legacy.read_text(encoding="utf-8") == original

    def test_returns_none_when_no_project_dir(self, monkeypatch):
        """Empty CLAUDE_PROJECT_DIR → None."""
        from shared.claude_md_manager import update_pact_routing

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        assert update_pact_routing() is None

    def test_returns_none_when_file_does_not_exist(self, tmp_path, monkeypatch):
        """Project dir exists but no CLAUDE.md → defer to ensure_project_memory_md."""
        from shared.claude_md_manager import update_pact_routing

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result is None
        # update_pact_routing must not create the file
        assert not (tmp_path / ".claude" / "CLAUDE.md").exists()
        assert not (tmp_path / "CLAUDE.md").exists()


class TestUpdatePactRoutingUpdate:
    """Markers present, but content between them is stale → replace it."""

    def test_replaces_stale_content_between_markers(self, tmp_path, monkeypatch):
        """Stale routing block content gets replaced with canonical version."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text(
            "# Project Memory\n"
            "\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "## OLD ROUTING CONTENT\n"
            "Outdated instructions here\n"
            "<!-- PACT_ROUTING_END -->\n"
            "\n"
            "## Working Memory\n"
            "user notes\n",
            encoding="utf-8",
        )

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result == "PACT routing block updated in project CLAUDE.md"
        new_content = legacy.read_text(encoding="utf-8")
        # Canonical block is now present
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content
        # Stale content gone
        assert "OLD ROUTING CONTENT" not in new_content
        assert "Outdated instructions here" not in new_content
        # Surrounding content preserved
        assert "# Project Memory" in new_content
        assert "## Working Memory" in new_content
        assert "user notes" in new_content

    def test_secure_permissions_after_update(self, tmp_path, monkeypatch):
        """Updated file must end up at 0o600."""
        import stat
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text(
            "# Project Memory\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "stale\n"
            "<!-- PACT_ROUTING_END -->\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        update_pact_routing()

        mode = stat.S_IMODE(legacy.stat().st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


class TestUpdatePactRoutingInsert:
    """Markers absent — insert the block near the top of the file."""

    def test_inserts_block_after_title(self, tmp_path, monkeypatch):
        """Routing block is inserted after the # title line."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        original = (
            "# Project Memory\n"
            "\n"
            "## Working Memory\n"
            "user notes that must survive\n"
        )
        legacy.write_text(original, encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result == "PACT routing block inserted into project CLAUDE.md"
        new_content = legacy.read_text(encoding="utf-8")
        # Canonical block now present
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content
        # Original content preserved
        assert "# Project Memory" in new_content
        assert "## Working Memory" in new_content
        assert "user notes that must survive" in new_content
        # Block sits between the title and the next section
        title_idx = new_content.index("# Project Memory")
        block_idx = new_content.index("<!-- PACT_ROUTING_START")
        wm_idx = new_content.index("## Working Memory")
        assert title_idx < block_idx < wm_idx

    def test_idempotent_after_insert(self, tmp_path, monkeypatch):
        """A second invocation after insert must be a no-op."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text(
            "# Project Memory\n\n## Working Memory\nuser notes\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        first = update_pact_routing()
        assert first == "PACT routing block inserted into project CLAUDE.md"

        # Second call must not write again
        second = update_pact_routing()
        assert second is None


class TestUpdatePactRoutingOSError:
    """OSError on read or write paths → graceful failure, status string returned.

    Cycle 2 minor item 5/6: exercises the try/except OSError fallback
    branches in update_pact_routing. Previously these branches were
    unexercised, so a bug in the error handling (wrong format string,
    wrong truncation length, accidentally raising instead of returning)
    would not be caught by CI.
    """

    def test_returns_none_when_read_fails(self, tmp_path, monkeypatch):
        """OSError on read_text → returns None (file appears unreadable)."""
        from unittest.mock import patch
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("placeholder content", encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        original_read_text = Path.read_text

        def selective_read_text(self, *args, **kwargs):
            if str(self) == str(legacy):
                raise OSError("simulated read failure")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", selective_read_text):
            result = update_pact_routing()

        assert result is None

    def test_returns_failure_status_when_write_fails_during_update(
        self, tmp_path, monkeypatch
    ):
        """OSError on write_text during the update path → returns failure status."""
        from unittest.mock import patch
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        # Stale content between markers — triggers the update path
        original = (
            "# Project Memory\n"
            "\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "## PACT Routing\n\nstale content that should be replaced\n"
            "<!-- PACT_ROUTING_END -->\n"
            "\n"
            "## Working Memory\n"
        )
        legacy.write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        original_write_text = Path.write_text

        def selective_write_text(self, *args, **kwargs):
            if str(self) == str(legacy):
                raise OSError("simulated write failure during update")
            return original_write_text(self, *args, **kwargs)

        with patch.object(Path, "write_text", selective_write_text):
            result = update_pact_routing()

        assert result is not None
        assert "Failed to update PACT routing" in result
        # Original file unchanged because write was blocked
        assert legacy.read_text(encoding="utf-8") == original

    def test_returns_failure_status_when_write_fails_during_insert(
        self, tmp_path, monkeypatch
    ):
        """OSError on write_text during the insert path → returns failure status."""
        from unittest.mock import patch
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        # No markers — triggers the insert path
        original = "# Project Memory\n\n## Working Memory\nuser notes\n"
        legacy.write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        original_write_text = Path.write_text

        def selective_write_text(self, *args, **kwargs):
            if str(self) == str(legacy):
                raise OSError("simulated write failure during insert")
            return original_write_text(self, *args, **kwargs)

        with patch.object(Path, "write_text", selective_write_text):
            result = update_pact_routing()

        assert result is not None
        assert "Failed to insert PACT routing" in result
        # Original file unchanged because write was blocked
        assert legacy.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# ensure_project_memory_md() — preserved tests + canonical-block check
# ---------------------------------------------------------------------------

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
        assert "# Project Memory" in content
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

    def test_created_file_contains_canonical_routing_block(self, tmp_path, monkeypatch):
        """The created file must embed the canonical _PACT_ROUTING_BLOCK verbatim."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        assert CANONICAL_PACT_ROUTING_BLOCK in content

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


class TestMarkerConsistency:
    """Spec Section 8: cross-file fixture sanity check.

    The _PACT_ROUTING_BLOCK constant in claude_md_manager.py pattern-matches
    against two role-marker substrings to route agents to the correct
    bootstrap skill:

      - `PACT ROLE: orchestrator` → PACT:bootstrap
      - `PACT ROLE: teammate (`   → PACT:teammate-bootstrap

    Meanwhile, three production sites emit these markers:

      - session_init.py `_team_create` / `_team_reuse` emit the
        orchestrator marker to fresh and resumed lead sessions.
      - peer_inject.py `_BOOTSTRAP_PRELUDE_TEMPLATE` emits the teammate
        marker to every newly spawned teammate via SubagentStart hook.

    The marker literals on both sides are plain strings in three
    different Python files. Nothing except tests prevents someone from
    editing the routing block's search patterns without also editing
    the hook emissions (or vice versa). A single-character drift silently
    breaks routing — the unit tests still pass because each side is
    internally consistent, but `_PACT_ROUTING_BLOCK`'s guidance would
    point at a substring the hooks never actually emit.

    This test asserts the emitted strings contain the exact substrings
    the routing block searches for. Catches drift between the two files.
    """

    HOOKS_DIR = Path(__file__).parent.parent / "hooks"
    SESSION_INIT_PATH = HOOKS_DIR / "session_init.py"
    BOOTSTRAP_MD_PATH = (
        Path(__file__).parent.parent / "commands" / "bootstrap.md"
    )

    ORCHESTRATOR_MARKER = "PACT ROLE: orchestrator"
    TEAMMATE_MARKER_PREFIX = "PACT ROLE: teammate ("

    @staticmethod
    def _bootstrap_md_dispatch_region(text: str) -> str:
        """Slice the Agent Teams Dispatch callout region out of bootstrap.md.

        Mirrors TestDispatchTemplatePrelude._dispatch_region in
        test_agents_structure.py — same `MANDATORY` anchor, same ~80-line
        window. Duplicated locally so this test file has no cross-file
        import dependency on the sibling test module.
        """
        marker = "MANDATORY"
        idx = text.find(marker)
        if idx == -1:
            return ""
        tail = text[idx:]
        lines = tail.splitlines()[:80]
        return "\n".join(lines)

    def test_routing_block_contains_orchestrator_marker(self):
        """_PACT_ROUTING_BLOCK must reference `PACT ROLE: orchestrator`."""
        from shared.claude_md_manager import _PACT_ROUTING_BLOCK

        assert self.ORCHESTRATOR_MARKER in _PACT_ROUTING_BLOCK, (
            f"_PACT_ROUTING_BLOCK is missing the `{self.ORCHESTRATOR_MARKER}` "
            f"substring — routing logic in spawned leads cannot match "
            f"what the hooks emit."
        )

    def test_routing_block_contains_teammate_marker_prefix(self):
        """_PACT_ROUTING_BLOCK must reference `PACT ROLE: teammate (`."""
        from shared.claude_md_manager import _PACT_ROUTING_BLOCK

        assert self.TEAMMATE_MARKER_PREFIX in _PACT_ROUTING_BLOCK, (
            f"_PACT_ROUTING_BLOCK is missing the "
            f"`{self.TEAMMATE_MARKER_PREFIX}` substring — routing logic "
            f"in spawned teammates cannot match what peer_inject emits."
        )

    def test_session_init_team_create_emits_orchestrator_marker(self):
        """session_init.py's `_team_create` string literal must contain
        the exact orchestrator marker that the routing block searches for.

        `_team_create` is a local variable inside a function so we can't
        import it — assert via source-text read instead.
        """
        source = self.SESSION_INIT_PATH.read_text(encoding="utf-8")
        # Locate the _team_create assignment and verify the orchestrator
        # marker appears within the literal that follows.
        assert "_team_create = (" in source, (
            "session_init.py is missing the `_team_create` assignment — "
            "schema drift since this test was written. Update the test "
            "anchor."
        )
        create_idx = source.find("_team_create = (")
        # Take a 2000-char window starting at the assignment to cover
        # the full string literal (which is ~600 chars).
        create_region = source[create_idx : create_idx + 2000]
        assert self.ORCHESTRATOR_MARKER in create_region, (
            f"session_init.py `_team_create` string literal must contain "
            f"`{self.ORCHESTRATOR_MARKER}` so fresh lead sessions are "
            f"routed to the orchestrator bootstrap. Routing-block search "
            f"pattern drift."
        )

    def test_session_init_team_reuse_emits_orchestrator_marker(self):
        """session_init.py's `_team_reuse` string literal must contain
        the exact orchestrator marker that the routing block searches for.
        """
        source = self.SESSION_INIT_PATH.read_text(encoding="utf-8")
        assert "_team_reuse = (" in source, (
            "session_init.py is missing the `_team_reuse` assignment — "
            "schema drift since this test was written. Update the test "
            "anchor."
        )
        reuse_idx = source.find("_team_reuse = (")
        reuse_region = source[reuse_idx : reuse_idx + 2000]
        assert self.ORCHESTRATOR_MARKER in reuse_region, (
            f"session_init.py `_team_reuse` string literal must contain "
            f"`{self.ORCHESTRATOR_MARKER}` so resumed lead sessions are "
            f"routed to the orchestrator bootstrap. Routing-block search "
            f"pattern drift."
        )

    def test_peer_inject_prelude_template_emits_teammate_marker(self):
        """peer_inject.py's `_BOOTSTRAP_PRELUDE_TEMPLATE` must, after
        format() substitution, contain the exact teammate marker prefix
        the routing block searches for.
        """
        from peer_inject import _BOOTSTRAP_PRELUDE_TEMPLATE

        rendered = _BOOTSTRAP_PRELUDE_TEMPLATE.format(agent_name="sample-agent")
        assert self.TEAMMATE_MARKER_PREFIX in rendered, (
            f"peer_inject.py `_BOOTSTRAP_PRELUDE_TEMPLATE` (after format) "
            f"must contain `{self.TEAMMATE_MARKER_PREFIX}` so spawned "
            f"teammates are routed to the teammate bootstrap. Routing-"
            f"block search pattern drift."
        )

    def test_bootstrap_md_dispatch_template_emits_teammate_marker(self):
        """The Agent Teams Dispatch template in bootstrap.md is the FOURTH
        production emission site for the teammate marker (alongside
        session_init.py _team_create/_team_reuse and peer_inject.py
        _BOOTSTRAP_PRELUDE_TEMPLATE — though session_init emits the
        orchestrator marker, not the teammate marker).

        The dispatch template is how the lead spawns specialists as
        teammates: the `prompt=` parameter of the `Task(...)` call embeds
        `PACT ROLE: teammate ({name})` so the spawned teammate's context
        carries the marker the routing block searches for. If the
        template drifts and drops the marker, spawned teammates will not
        self-bootstrap via the routing block and will lack team-protocol
        context — silent breakage.

        A sibling test in test_agents_structure.py::TestDispatchTemplatePrelude
        asserts the exact placeholder form `PACT ROLE: teammate ({name})`.
        This test adds a coarser cross-file-invariant check inside the
        TestMarkerConsistency class so the fourth emission site is
        visible in the same place as the other three.
        """
        text = self.BOOTSTRAP_MD_PATH.read_text(encoding="utf-8")
        region = self._bootstrap_md_dispatch_region(text)
        assert region, (
            "bootstrap.md missing the Agent Teams Dispatch `MANDATORY` "
            "callout anchor — cannot locate dispatch template region."
        )
        assert self.TEAMMATE_MARKER_PREFIX in region, (
            f"bootstrap.md Agent Teams Dispatch template must contain "
            f"`{self.TEAMMATE_MARKER_PREFIX}` inside the dispatch region "
            f"so teammates spawned via the dispatch pattern receive the "
            f"marker the routing block searches for. Routing-block "
            f"search pattern drift — spawned teammates will not "
            f"self-bootstrap."
        )

    def test_marker_consistency_end_to_end(self):
        """End-to-end tripwire: every marker substring the routing block
        searches for must be emitted by EVERY production site registered
        below. Acts as a tripwire if someone:
          (a) adds a new marker pattern to the routing block without
              wiring up a corresponding emitter, OR
          (b) drops the marker from ANY single emitter while the other
              emitters still hold the line.

        The (b) case matters because the PACT routing architecture is
        multi-layer: the lead session path (session_init), the spawned
        teammate path via hook injection (peer_inject), and the spawned
        teammate path via dispatch template (bootstrap.md) are all
        independently load-bearing. A silent drop in any one of them
        breaks a specific code path without the unit tests on the other
        paths noticing — which is exactly the kind of drift this
        tripwire exists to catch.

        Note that session_init emits the ORCHESTRATOR marker (to lead
        sessions), while peer_inject and bootstrap.md emit the TEAMMATE
        marker prefix (to spawned teammates). That split is intentional
        — the routing block uses each marker to dispatch to a different
        bootstrap skill.
        """
        from shared.claude_md_manager import _PACT_ROUTING_BLOCK
        from peer_inject import _BOOTSTRAP_PRELUDE_TEMPLATE

        session_init_source = self.SESSION_INIT_PATH.read_text(encoding="utf-8")
        rendered_prelude = _BOOTSTRAP_PRELUDE_TEMPLATE.format(
            agent_name="sample-agent"
        )
        bootstrap_md_text = self.BOOTSTRAP_MD_PATH.read_text(encoding="utf-8")
        bootstrap_md_dispatch_region = self._bootstrap_md_dispatch_region(
            bootstrap_md_text
        )
        assert bootstrap_md_dispatch_region, (
            "bootstrap.md missing the Agent Teams Dispatch `MANDATORY` "
            "callout anchor — cannot locate dispatch template region."
        )

        # For each marker the routing block searches for, verify EVERY
        # registered production emission site contains it. Missing from
        # even one emitter fires the tripwire.
        marker_to_emitters = {
            self.ORCHESTRATOR_MARKER: [
                ("session_init.py (_team_create/_team_reuse)", session_init_source),
            ],
            self.TEAMMATE_MARKER_PREFIX: [
                ("peer_inject.py (_BOOTSTRAP_PRELUDE_TEMPLATE)", rendered_prelude),
                ("bootstrap.md (Agent Teams Dispatch template)", bootstrap_md_dispatch_region),
            ],
        }

        for marker, emitters in marker_to_emitters.items():
            assert marker in _PACT_ROUTING_BLOCK, (
                f"Routing block does not search for `{marker}` — test "
                f"fixture is stale. Update the test or the routing block."
            )
            missing = [
                name for name, source in emitters if marker not in source
            ]
            assert not missing, (
                f"Routing block searches for `{marker}` but the following "
                f"production emission site(s) do not contain it: "
                f"{missing}. Registered emitters: "
                f"{[name for name, _ in emitters]}. Routing is broken "
                f"for this code path — a teammate or lead reaching the "
                f"broken emitter will not self-bootstrap."
            )
