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
    "Check your context for a `PACT ROLE:` marker AT THE START OF A LINE (not\n"
    "embedded in prose, quoted text, or memory-retrieval results). Hook\n"
    "injections from `session_init.py` and `peer_inject.py` always emit the\n"
    "marker at the start of a line, so a line-anchored substring check is\n"
    "the trustworthy form. Mid-line occurrences of the phrase (e.g., from\n"
    "pinned notes about PACT architecture, retrieved memories that quote the\n"
    "marker, or documentation snippets) are NOT valid signals and must be\n"
    "ignored.\n"
    "\n"
    "- Line starting with `PACT ROLE: orchestrator` \u2192 invoke `Skill(\"PACT:bootstrap\")` unless already loaded.\n"
    "- Line starting with `PACT ROLE: teammate (` \u2192 invoke `Skill(\"PACT:teammate-bootstrap\")` unless already loaded.\n"
    "\n"
    "No line-anchored marker present? Inspect your system prompt: a\n"
    "`# Custom Agent Instructions` block naming a specific PACT agent means\n"
    "you are a teammate (invoke the teammate bootstrap); otherwise you are\n"
    "the main session (invoke the orchestrator bootstrap).\n"
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

        # Two bullet rows; both use U+2192. After cycle 2 item 15
        # line-anchor mitigation, the bullets are phrased as "Line
        # starting with `PACT ROLE: ...`" rather than the bare marker.
        assert _PACT_ROUTING_BLOCK.count("\u2192") == 2
        assert "Line starting with `PACT ROLE: orchestrator` \u2192" in _PACT_ROUTING_BLOCK
        assert "Line starting with `PACT ROLE: teammate (` \u2192" in _PACT_ROUTING_BLOCK

    def test_line_anchor_heuristic_rejects_mid_line_pact_role(self):
        """The routing block instructs agents to check for 'PACT ROLE:'
        AT THE START OF A LINE. A mid-line occurrence (e.g., inside a
        Working Memory section quoting the marker) must NOT be treated
        as a valid role signal.

        This test simulates the consumer-side heuristic described in the
        routing block text: split context into lines, check each line
        with startswith('PACT ROLE:'). A CLAUDE.md with the marker
        embedded mid-line in Working Memory should produce zero matches.
        """
        claude_md_content = (
            "# Project Memory\n"
            "\n"
            "## Working Memory\n"
            "- 2026-04-12: The session_init hook injects PACT ROLE: orchestrator "
            "into additionalContext for the lead session.\n"
            "- Architecture note: PACT ROLE: teammate markers are injected by "
            "peer_inject.py.\n"
            "\n"
            "## Retrieved Context\n"
            "- Memory 0a52fd73: session_init emits `PACT ROLE: orchestrator` at "
            "byte 0 of additionalContext\n"
        )

        # Simulate the consumer-side line-anchored check
        line_anchored_matches = [
            line for line in claude_md_content.splitlines()
            if line.startswith("PACT ROLE:")
        ]

        assert line_anchored_matches == [], (
            f"Line-anchored check found false-positive PACT ROLE markers in "
            f"Working Memory / Retrieved Context sections: {line_anchored_matches}. "
            f"The routing block instructs agents to use a line-anchored check — "
            f"mid-line occurrences must not match."
        )


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

    def test_returns_skip_status_when_only_end_marker(self, mock_home):
        """PACT_END alone (no START) → returns a 'Migration skipped' status
        string so session_init.py surfaces the warning to the user via
        context_parts. Content is unchanged (defensive no-op)."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        original = "user content\n<!-- PACT_END -->\nmore content\n"
        target.write_text(original, encoding="utf-8")

        result = remove_stale_kernel_block()

        assert result is not None
        assert "Migration skipped" in result
        assert "PACT_END" in result
        assert "PACT_START" in result
        # File is unchanged (defensive no-op)
        assert target.read_text(encoding="utf-8") == original


class TestRemoveStaleKernelBlockMalformed:
    """Malformed marker states — defensive no-op PLUS a status string
    returned so session_init.py surfaces the warning via context_parts.
    Previously the defensive paths emitted stderr warnings (never shown
    to the user) and returned None; now they return the warning string
    so it actually reaches the user's orchestrator context."""

    def test_returns_skip_status_when_start_without_end(self, mock_home):
        """Unterminated PACT block → defensive no-op, returns 'Migration skipped'."""
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

        assert result is not None
        assert "Migration skipped" in result
        assert "PACT_START" in result
        assert "PACT_END" in result
        # File is unchanged (defensive no-op)
        assert target.read_text(encoding="utf-8") == original

    def test_returns_skip_status_when_end_appears_before_start(self, mock_home):
        """END marker appears textually before START → defensive no-op +
        'Migration skipped' string."""
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

        assert result is not None
        assert "Migration skipped" in result
        # The function splits on START first then checks for END in the
        # remainder. Here END is before START so the remainder has no END.
        # Content remains untouched.
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


class TestUpdatePactRoutingOrphanMarkers:
    """Cycle 2 minor item 13: orphan marker handling.

    If exactly one of PACT_ROUTING_START or PACT_ROUTING_END is present
    (e.g., user manually deleted the closing marker, or a prior write
    crashed mid-file), the function strips the orphan marker before
    falling through to the insert path. Without this fix, the file
    would accumulate a new routing block on every session because the
    update guard requires BOTH markers.
    """

    def test_orphan_start_marker_stripped_before_insert(
        self, tmp_path, monkeypatch
    ):
        """Orphan PACT_ROUTING_START with no matching END → orphan stripped,
        fresh canonical block inserted, no accumulation on subsequent runs."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        # PACT_ROUTING_START present alone (orphan), no END marker
        original = (
            "# Project Memory\n"
            "\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "## PACT Routing\n\nstale orphan content with no closing marker\n"
            "\n"
            "## Working Memory\n"
            "user notes\n"
        )
        legacy.write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        # First call: insert path (after orphan strip) → file gets canonical block.
        # The return string now includes the orphan-stripped notice.
        assert result is not None
        assert "PACT routing block inserted into project CLAUDE.md" in result
        assert "orphan" in result.lower()
        new_content = legacy.read_text(encoding="utf-8")

        # The orphan START marker line is gone (stripped before insertion)
        # and the canonical routing block is now present (with both markers)
        assert new_content.count(
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
        ) == 1, (
            "Should have exactly 1 PACT_ROUTING_START marker after fix "
            "(the new canonical one). Orphan was not stripped."
        )
        assert new_content.count("<!-- PACT_ROUTING_END -->") == 1
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content
        # User content preserved
        assert "## Working Memory" in new_content
        assert "user notes" in new_content
        # Orphan content body was inside the orphan marker block — it remains
        # because orphan stripping only removes the marker line itself, not
        # the surrounding text. This is intentional — preserves user data.

        # Second call: idempotent no-op (markers now well-formed)
        second = update_pact_routing()
        assert second is None, (
            "Second call should be a no-op. If this fails, the orphan-strip "
            "+ insert path is not converging on canonical state."
        )

    def test_orphan_end_marker_stripped_before_insert(
        self, tmp_path, monkeypatch
    ):
        """Orphan PACT_ROUTING_END with no matching START → same handling."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        # Only END marker present, no START
        original = (
            "# Project Memory\n"
            "\n"
            "stale content with stray closing marker\n"
            "<!-- PACT_ROUTING_END -->\n"
            "\n"
            "## Working Memory\n"
        )
        legacy.write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        # Insert path fires after orphan strip — return string includes
        # the orphan-stripped notice.
        assert result is not None
        assert "PACT routing block inserted into project CLAUDE.md" in result
        assert "orphan" in result.lower()
        new_content = legacy.read_text(encoding="utf-8")
        assert new_content.count("<!-- PACT_ROUTING_END -->") == 1
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content

        # Subsequent call is a no-op
        assert update_pact_routing() is None

    def test_no_accumulation_on_repeated_calls_with_orphan(
        self, tmp_path, monkeypatch
    ):
        """The fix's purpose: subsequent sessions with the orphan-stripped
        file must NOT accumulate additional routing blocks."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text(
            "# Project Memory\n\n<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\norphan body\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # First call: orphan strip + insert
        update_pact_routing()
        # Second call: idempotent no-op
        update_pact_routing()
        # Third call: idempotent no-op
        update_pact_routing()

        final = legacy.read_text(encoding="utf-8")
        # Exactly one of each marker — no accumulation
        assert final.count(
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
        ) == 1
        assert final.count("<!-- PACT_ROUTING_END -->") == 1


class TestUpdatePactRoutingSessionStartIsolation:
    """SESSION_START preservation tripwire (#366 item 5, architect S3 finding).

    update_pact_routing and update_session_info both mutate the project
    CLAUDE.md but use disjoint markers (PACT_ROUTING_START/END vs
    SESSION_START/END). The current code relies on marker disjointness
    to avoid clobbering the session block, but there is no tripwire test
    asserting that:

    1. The insert path (markers absent) leaves the SESSION_START block
       byte-identical.
    2. The update path (markers canonicalized) leaves the SESSION_START
       block byte-identical.
    3. The orphan-strip path does NOT reach into a SESSION_START block
       even if the session body happens to contain a line that matches
       the routing marker substring.

    Test (3) is the worst-case scenario architect flagged. Without the
    SESSION_START isolation in the orphan-strip loop, a file whose
    SESSION_START body accidentally contained a line matching the
    PACT_ROUTING_START marker would be silently corrupted: the orphan
    strip would drop the line from inside the session body, and the
    subsequent insert path would add a fresh routing block at the top,
    leaving SESSION_START missing a line. The fix in claude_md_manager
    tracks inside_session_block and preserves those lines verbatim."""

    SESSION_BLOCK_BODY = (
        "<!-- SESSION_START -->\n"
        "## Current Session\n"
        "- Resume: `claude --resume deadbeef-dead-beef-dead-beefdeadbeef`\n"
        "- Team: `pact-deadbeef`\n"
        "- Session dir: `/Users/test/.claude/pact-sessions/proj/deadbeef`\n"
        "- Started: 2026-04-11 00:00:00 UTC\n"
        "<!-- SESSION_END -->\n"
    )

    def test_insert_path_preserves_session_start_block_verbatim(
        self, tmp_path, monkeypatch
    ):
        """Insert path: file has SESSION_START but NO PACT_ROUTING markers.
        After update_pact_routing, a PACT_ROUTING block is inserted AND the
        SESSION_START block is byte-identical to what was there before."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        original_content = (
            "# Project Memory\n"
            "\n"
            "Some preamble that is not session metadata.\n"
            "\n"
            + self.SESSION_BLOCK_BODY
            + "\n"
            "## Retrieved Context\n"
            "(empty)\n"
        )
        legacy.write_text(original_content, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        new_content = legacy.read_text(encoding="utf-8")

        # 1. PACT_ROUTING block was inserted
        assert result is not None
        assert "inserted" in result.lower()
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content

        # 2. SESSION_START block body is byte-identical
        assert self.SESSION_BLOCK_BODY in new_content, (
            "SESSION_START block body must be byte-identical after "
            "update_pact_routing inserts a routing block."
        )

        # 3. Sensible position — SESSION_START block is not fragmented
        start_idx = new_content.index("<!-- SESSION_START -->")
        end_idx = new_content.index("<!-- SESSION_END -->")
        assert start_idx < end_idx
        # The routing block should be placed before SESSION_START (near top)
        routing_idx = new_content.index(
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
        )
        assert routing_idx < start_idx, (
            "PACT_ROUTING block should be inserted before the "
            "SESSION_START block, not inside or after it."
        )

    def test_update_path_preserves_session_start_block_verbatim(
        self, tmp_path, monkeypatch
    ):
        """Update path: file has BOTH a non-canonical PACT_ROUTING block
        AND a SESSION_START block. After update_pact_routing, the routing
        block is canonicalized AND SESSION_START is byte-identical."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        original_content = (
            "# Project Memory\n"
            "\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "## PACT Routing\n"
            "\n"
            "STALE non-canonical content that should be rewritten.\n"
            "<!-- PACT_ROUTING_END -->\n"
            "\n"
            + self.SESSION_BLOCK_BODY
            + "\n"
            "## Retrieved Context\n"
            "(empty)\n"
        )
        legacy.write_text(original_content, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        new_content = legacy.read_text(encoding="utf-8")

        # 1. Routing block canonicalized
        assert result is not None
        assert "updated" in result.lower()
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content
        assert "STALE non-canonical content" not in new_content

        # 2. SESSION_START block body byte-identical
        assert self.SESSION_BLOCK_BODY in new_content, (
            "SESSION_START block body must be byte-identical after "
            "update_pact_routing canonicalizes the routing block."
        )

    def test_orphan_strip_does_not_corrupt_session_start(
        self, tmp_path, monkeypatch
    ):
        """Worst-case tripwire: SESSION_START body contains a line matching
        the PACT_ROUTING_START marker, and there is NO matching END marker
        elsewhere in the file. This triggers the orphan-strip branch.

        Without the SESSION_START isolation fix in the orphan-strip loop,
        the loop would silently drop the matching line from inside the
        session body, then the insert path would prepend a new routing
        block at the top — leaving SESSION_START visibly corrupted.

        With the fix, lines inside SESSION_START/SESSION_END are preserved
        verbatim, and the insert path still adds a routing block at the
        top. Both blocks coexist cleanly."""
        from shared.claude_md_manager import update_pact_routing

        # SESSION_START body contains a line that is literally the routing
        # start marker — e.g., user pasted routing-block docs into the
        # session metadata. No PACT_ROUTING_END is present anywhere.
        session_block_with_marker = (
            "<!-- SESSION_START -->\n"
            "## Current Session\n"
            "- Resume: `claude --resume deadbeef`\n"
            "- Team: `pact-deadbeef`\n"
            "- Note: docs pasted below\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "- Started: 2026-04-11 00:00:00 UTC\n"
            "<!-- SESSION_END -->\n"
        )
        legacy = tmp_path / "CLAUDE.md"
        original_content = (
            "# Project Memory\n"
            "\n"
            + session_block_with_marker
            + "\n"
            "## Retrieved Context\n"
            "(empty)\n"
        )
        legacy.write_text(original_content, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()
        assert result is not None

        new_content = legacy.read_text(encoding="utf-8")

        # 1. SESSION_START block body byte-identical — the orphan strip
        # MUST NOT have dropped the line matching the routing marker.
        assert session_block_with_marker in new_content, (
            "SESSION_START block body was corrupted — the orphan strip "
            "reached into the session block and dropped a line. "
            "Expected byte-identical preservation of SESSION_START body."
        )

        # 2. A canonical routing block was added at the top (insert path)
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content

        # 3. All the session content is still readable and intact
        assert "- Resume: `claude --resume deadbeef`" in new_content
        assert "- Team: `pact-deadbeef`" in new_content
        assert "- Started: 2026-04-11 00:00:00 UTC" in new_content
        assert "- Note: docs pasted below" in new_content

    def test_orphan_strip_outside_session_block_still_works(
        self, tmp_path, monkeypatch
    ):
        """Regression guard for the SESSION_START isolation fix:
        orphan markers OUTSIDE a SESSION_START block must still be
        stripped. This ensures the fix did not over-scope and break the
        main orphan-strip behavior for content outside session metadata."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        # Orphan START marker outside any SESSION_START block
        original_content = (
            "# Project Memory\n"
            "\n"
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->\n"
            "\n"
            + self.SESSION_BLOCK_BODY
            + "\n"
            "## Retrieved Context\n"
            "(empty)\n"
        )
        legacy.write_text(original_content, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()
        assert result is not None

        new_content = legacy.read_text(encoding="utf-8")

        # Exactly one of each marker — the orphan outside SESSION_START
        # was stripped, and a fresh canonical block was inserted at top.
        assert new_content.count(
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
        ) == 1
        assert new_content.count("<!-- PACT_ROUTING_END -->") == 1

        # SESSION_START body still byte-identical
        assert self.SESSION_BLOCK_BODY in new_content


class TestUpdatePactRoutingStaleOrchestratorLine:
    """F1: strip the v3.16.2-era 'The global PACT Orchestrator is loaded
    from ~/.claude/CLAUDE.md' line from upgraded project CLAUDE.mds.

    After the #366 Phase 1 migration, the routing block supersedes the
    stale line. Leaving it in place creates a factual contradiction for
    users who upgrade. update_pact_routing must strip the line even when
    the routing block is already canonical (the original short-circuit
    return would otherwise leave the stale line in place forever)."""

    STALE_LINE = "The global PACT Orchestrator is loaded from `~/.claude/CLAUDE.md`."

    def test_strips_stale_line_when_block_already_canonical(
        self, tmp_path, monkeypatch
    ):
        """Stale line + canonical routing block → file is rewritten to
        drop the stale line; the canonical routing block is untouched."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        original = (
            "# Project Memory\n"
            "\n"
            f"{self.STALE_LINE}\n"
            "\n"
            f"{CANONICAL_PACT_ROUTING_BLOCK}\n"
            "\n"
            "## Working Memory\n"
        )
        legacy.write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result is not None
        assert "stale" in result.lower() or "orchestrator-loader" in result.lower()
        new_content = legacy.read_text(encoding="utf-8")
        assert self.STALE_LINE not in new_content
        # Canonical routing block survives intact
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content
        assert "# Project Memory" in new_content
        assert "## Working Memory" in new_content

    def test_strips_stale_line_when_inserting_routing_block(
        self, tmp_path, monkeypatch
    ):
        """Stale line + no routing block → stale line is removed AND the
        canonical routing block is inserted in the same pass. The return
        string mentions the stale-line strip as a suffix."""
        from shared.claude_md_manager import update_pact_routing

        legacy = tmp_path / "CLAUDE.md"
        original = (
            "# Project Memory\n"
            "\n"
            "This file contains project-specific memory managed by the PACT framework.\n"
            f"{self.STALE_LINE}\n"
            "\n"
            "## Working Memory\n"
            "user notes\n"
        )
        legacy.write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result is not None
        assert "inserted" in result
        assert "stale" in result.lower() or "orchestrator-loader" in result.lower()
        new_content = legacy.read_text(encoding="utf-8")
        assert self.STALE_LINE not in new_content
        assert CANONICAL_PACT_ROUTING_BLOCK in new_content
        # Unrelated user content preserved
        assert "This file contains project-specific memory" in new_content
        assert "## Working Memory" in new_content
        assert "user notes" in new_content

    def test_no_write_when_no_stale_line_and_block_canonical(
        self, tmp_path, monkeypatch
    ):
        """Fresh project CLAUDE.md (no stale line, canonical block) →
        idempotent no-op: returns None, file is byte-identical after."""
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
        mtime_before = legacy.stat().st_mtime_ns
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_pact_routing()

        assert result is None
        assert legacy.read_text(encoding="utf-8") == original
        assert legacy.stat().st_mtime_ns == mtime_before


class TestRemoveStaleKernelBlockBlankLinePreservation:
    """F3: preserve one blank line at the removal boundary when the
    obsolete kernel block is stripped. The pre-fix implementation
    collapsed intentional blank lines around the removed block, trampling
    user spacing."""

    def test_preserves_single_blank_line_between_pre_and_post(self, mock_home):
        """"Line1\\n\\n<block>\\n\\nLine2\\n" → "Line1\\n\\nLine2\\n" —
        one blank line survives the strip."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "Line1\n"
            "\n"
            "<!-- PACT_START: Managed by pact-plugin -->\n"
            "kernel body\n"
            "<!-- PACT_END -->\n"
            "\n"
            "Line2\n",
            encoding="utf-8",
        )

        remove_stale_kernel_block()

        new_content = target.read_text(encoding="utf-8")
        assert new_content == "Line1\n\nLine2\n"

    def test_block_at_top_of_file_leaves_clean_post_content(self, mock_home):
        """PACT block at the top of the file → post content starts fresh
        with no leading blank lines."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "<!-- PACT_START: Managed by pact-plugin -->\n"
            "kernel body\n"
            "<!-- PACT_END -->\n"
            "\n"
            "User content starts here\n"
            "more content\n",
            encoding="utf-8",
        )

        remove_stale_kernel_block()

        new_content = target.read_text(encoding="utf-8")
        assert new_content == "User content starts here\nmore content\n"

    def test_block_at_end_of_file_leaves_trailing_newline_on_pre(self, mock_home):
        """PACT block at the end of the file → file ends with pre_clean + '\\n',
        no leftover whitespace or markers."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "User content line 1\n"
            "User content line 2\n"
            "\n"
            "<!-- PACT_START: Managed by pact-plugin -->\n"
            "kernel body\n"
            "<!-- PACT_END -->\n",
            encoding="utf-8",
        )

        remove_stale_kernel_block()

        new_content = target.read_text(encoding="utf-8")
        assert new_content == "User content line 1\nUser content line 2\n"


class TestSymlinkRefusal:
    """SECURITY hardening — refuse to operate on symlinks. Both
    remove_stale_kernel_block and update_pact_routing return a status
    string ('Migration skipped: ...' or 'Routing skipped: ...') if their
    target is a symlink, rather than following the link and writing to
    its target. session_init.py routes these to context_parts so the
    user sees the warning via orchestrator context (hook stderr is NOT
    shown to users).

    The status strings are deliberately opaque: they name WHAT was skipped
    and use a generic "path precondition not met" phrase that does not
    reveal the internal guard (symlink check) to a local attacker reading
    the output. Tests assert on the opaque phrasing, not on the word
    "symlink" or "refusing".

    Tests use os.symlink to create real symlinks pointing at unrelated
    files in tmp_path. We verify (1) the function returns the opaque
    skip status string, (2) the symlink target file is byte-identical
    (untouched), and (3) the symlink itself still exists."""

    def test_remove_stale_kernel_block_refuses_symlink(
        self, mock_home, tmp_path
    ):
        """If ~/.claude/CLAUDE.md is a symlink, remove_stale_kernel_block
        returns an opaque 'Migration skipped: ... path precondition not met'
        string and does not touch the symlink target."""
        from shared.claude_md_manager import remove_stale_kernel_block

        # Create a regular file as the symlink target
        symlink_target = tmp_path / "external_target.md"
        symlink_target_content = (
            "# External target\n"
            "<!-- PACT_START: Managed by pact-plugin - do not edit -->\n"
            "fake kernel content that should NOT be touched\n"
            "<!-- PACT_END -->\n"
            "more external content\n"
        )
        symlink_target.write_text(symlink_target_content, encoding="utf-8")

        # Replace ~/.claude/CLAUDE.md with a symlink to the external target
        managed_path = mock_home / ".claude" / "CLAUDE.md"
        if managed_path.exists() or managed_path.is_symlink():
            managed_path.unlink()
        os.symlink(str(symlink_target), str(managed_path))
        assert managed_path.is_symlink()

        result = remove_stale_kernel_block()

        # Returns an opaque status string ("path precondition not met")
        # rather than one that discloses the symlink check to attackers.
        assert result is not None
        assert "Migration skipped" in result
        assert "path precondition not met" in result
        # Deliberately NOT revealing: the word "symlink" or "refusing"
        # should not appear in the status string.
        assert "symlink" not in result.lower()
        assert "refusing" not in result.lower()
        # Symlink target file is byte-identical (untouched)
        assert symlink_target.read_text(encoding="utf-8") == symlink_target_content
        # Symlink itself is still a symlink
        assert managed_path.is_symlink()

    def test_update_pact_routing_refuses_symlink(
        self, tmp_path, monkeypatch
    ):
        """If the project CLAUDE.md is a symlink, update_pact_routing returns
        an opaque 'Routing skipped: ... path precondition not met' string
        and does not touch the symlink target."""
        from shared.claude_md_manager import update_pact_routing

        # Create a regular file as the symlink target
        symlink_target = tmp_path / "external_claude.md"
        symlink_target_content = (
            "# External target\n"
            "user content that should NOT be touched\n"
        )
        symlink_target.write_text(symlink_target_content, encoding="utf-8")

        # Project CLAUDE.md is a symlink to the external target
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        managed_path = project_dir / "CLAUDE.md"
        os.symlink(str(symlink_target), str(managed_path))
        assert managed_path.is_symlink()

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        result = update_pact_routing()

        # Returns an opaque status string ("path precondition not met")
        # rather than one that discloses the symlink check to attackers.
        assert result is not None
        assert "Routing skipped" in result
        assert "path precondition not met" in result
        # Deliberately NOT revealing: the word "symlink" or "refusing"
        # should not appear in the status string.
        assert "symlink" not in result.lower()
        assert "refusing" not in result.lower()
        # Symlink target file is byte-identical (untouched)
        assert symlink_target.read_text(encoding="utf-8") == symlink_target_content
        # Symlink itself is still a symlink
        assert managed_path.is_symlink()

    def test_ensure_project_memory_md_refuses_dangling_symlink(
        self, tmp_path, monkeypatch
    ):
        """If the preferred .claude/CLAUDE.md path is a dangling symlink,
        ensure_project_memory_md returns an opaque skip status and does not
        follow the link.

        This covers the edge case where neither CLAUDE.md location exists
        (resolve returns "new_default") but the preferred path is a dangling
        symlink — e.g., a local attacker pre-planted a symlink before the
        first session. is_symlink uses lstat and returns True even for
        dangling links."""
        from shared.claude_md_manager import ensure_project_memory_md

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        dot_claude = project_dir / ".claude"
        dot_claude.mkdir()

        # Create a dangling symlink at the preferred CLAUDE.md path
        managed_path = dot_claude / "CLAUDE.md"
        os.symlink("/nonexistent/target", str(managed_path))
        assert managed_path.is_symlink()
        assert not managed_path.exists()  # dangling

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        result = ensure_project_memory_md()

        assert result is not None
        assert "Project CLAUDE.md skipped" in result
        assert "path precondition not met" in result
        assert "symlink" not in result.lower()
        assert "refusing" not in result.lower()
        # Symlink is still a dangling symlink (not replaced with a file)
        assert managed_path.is_symlink()
        assert not managed_path.exists()

    def test_update_session_info_refuses_symlink(
        self, tmp_path, monkeypatch
    ):
        """If the project CLAUDE.md is a symlink, update_session_info returns
        an opaque skip status and does not touch the symlink target.

        Placed in TestSymlinkRefusal alongside the other two guards for
        discoverability, with a parallel test in test_session_resume.py
        for the session_resume test suite."""
        from shared.session_resume import update_session_info

        symlink_target = tmp_path / "external_target.md"
        symlink_target_content = (
            "# External\n"
            "<!-- SESSION_START -->\n"
            "## Current Session\n"
            "<!-- SESSION_END -->\n"
        )
        symlink_target.write_text(symlink_target_content, encoding="utf-8")

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        managed_path = project_dir / "CLAUDE.md"
        os.symlink(str(symlink_target), str(managed_path))
        assert managed_path.is_symlink()

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        result = update_session_info("sess-new", "pact-new")

        assert result is not None
        assert "Session info skipped" in result
        assert "path precondition not met" in result
        assert "symlink" not in result.lower()
        assert "refusing" not in result.lower()
        assert symlink_target.read_text(encoding="utf-8") == symlink_target_content
        assert managed_path.is_symlink()


class TestRemoveStaleKernelBlockMalformedFeedback:
    """Malformed-marker user-visible feedback.

    When ~/.claude/CLAUDE.md contains an orphan marker (one of
    PACT_START/PACT_END but not the other, or both with END before START),
    remove_stale_kernel_block returns a 'Migration skipped: ...' status
    string explaining what was wrong and what the user should do. Hook
    stderr is NOT shown to users by Claude Code, so a returned string is
    the only way to deliver the warning. session_init.py routes these
    status strings to context_parts for user visibility via the
    orchestrator's context.

    Normal (well-formed) case returns the success message with no noise."""

    def test_orphan_start_marker_returns_skip_status(
        self, mock_home
    ):
        """Only PACT_START present → returns 'Migration skipped: ...' string
        mentioning PACT_START and PACT_END for user diagnosis."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "before\n<!-- PACT_START: Managed by pact-plugin -->\nbody\n",
            encoding="utf-8",
        )

        result = remove_stale_kernel_block()

        assert result is not None
        assert "Migration skipped" in result
        assert "PACT_START" in result
        assert "PACT_END" in result
        assert "orphan" in result.lower() or "matching" in result.lower()

    def test_orphan_end_marker_returns_skip_status(
        self, mock_home
    ):
        """Only PACT_END present → returns 'Migration skipped: ...' string."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "before\n<!-- PACT_END -->\nstray\n",
            encoding="utf-8",
        )

        result = remove_stale_kernel_block()

        assert result is not None
        assert "Migration skipped" in result
        assert "PACT_END" in result
        assert "PACT_START" in result

    def test_well_formed_block_does_not_return_skip_status(
        self, mock_home
    ):
        """Normal case (well-formed block) → clean success message, no
        'Migration skipped' noise."""
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "before\n"
            "<!-- PACT_START: Managed by pact-plugin - do not edit -->\n"
            "kernel body\n"
            "<!-- PACT_END -->\n"
            "after\n",
            encoding="utf-8",
        )

        result = remove_stale_kernel_block()

        assert result == "Removed obsolete PACT kernel block from ~/.claude/CLAUDE.md"
        # Normal case: no 'Migration skipped' in the success message
        assert "Migration skipped" not in result
        assert "Refusing" not in result


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

    def test_lock_timeout_returns_skip_message(self, tmp_path, monkeypatch):
        """C: when file_lock raises TimeoutError, ensure_project_memory_md
        must return the human-readable skip message and NOT create the file.

        Coverage gap closed: the existing TestUpdatePactRoutingLockContention
        suite exercises the analogous TimeoutError fail-open in
        update_pact_routing, but no test exercises the equivalent path inside
        ensure_project_memory_md. A regression here would mean a stuck lock
        (concurrent session_init hooks) crashes session start instead of
        skipping the project CLAUDE.md creation gracefully.

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


# ---------------------------------------------------------------------------
# #366 F1: File locking retrofit for concurrent SessionStart safety
# ---------------------------------------------------------------------------
# These tests cover the file_lock context manager and the concurrent-write
# behavior of remove_stale_kernel_block() and update_pact_routing().
#
# Why this matters: both functions perform read-mutate-write on managed
# CLAUDE.md files. Without a lock, two concurrent session_init hooks
# (e.g., user resumes session A in one window + starts session B in another
# on the same project) can interleave, and the last writer wins. Before
# this retrofit, update_session_info's SESSION_START block could be clobbered
# by a concurrent update_pact_routing write. Sidecar fcntl lock serializes
# the critical section.


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


class TestRemoveStaleKernelBlockLocking:
    """Concurrent remove_stale_kernel_block calls do not corrupt the file."""

    def test_concurrent_writes_preserve_managed_block(self, mock_home):
        """Two concurrent threads running remove_stale_kernel_block on the
        same home CLAUDE.md must converge to a clean, valid final state.

        Both threads start with the same input (markers present). With the
        sidecar lock, the read-mutate-write is serialized: the second
        thread sees the already-migrated content and is an idempotent
        no-op. Final state must contain user content verbatim, with no
        markers remaining.
        """
        import threading
        from shared.claude_md_manager import remove_stale_kernel_block

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "User preamble\n"
            "<!-- PACT_START: Managed by pact-plugin -->\n"
            "stale kernel body\n"
            "<!-- PACT_END -->\n"
            "User trailing\n",
            encoding="utf-8",
        )

        results: list[str | None] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=5)
                results.append(remove_stale_kernel_block())
            except BaseException as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == 2

        # Final file must be well-formed: user content preserved, no markers
        final = target.read_text(encoding="utf-8")
        assert "User preamble" in final
        assert "User trailing" in final
        assert "PACT_START" not in final
        assert "PACT_END" not in final
        assert "stale kernel body" not in final

        # Exactly one of the two workers did the removal work; the other
        # saw the already-migrated content and returned None (idempotent).
        # Order is non-deterministic, so just assert the multiset.
        assert results.count(
            "Removed obsolete PACT kernel block from ~/.claude/CLAUDE.md"
        ) == 1
        assert results.count(None) == 1


class TestUpdatePactRoutingLocking:
    """Concurrent update_pact_routing calls do not corrupt the project file."""

    def test_concurrent_writes_preserve_managed_block(
        self, tmp_path, monkeypatch
    ):
        """Two concurrent threads running update_pact_routing on the same
        project CLAUDE.md must converge to a clean, valid final state.

        Both threads start with a file containing the stale orchestrator
        line and NO routing block. With the sidecar lock, the read-mutate-
        write is serialized: exactly one thread does the insert, the
        other sees the canonical state and is a no-op (modulo idempotent
        re-write). Final state must contain the canonical routing block
        exactly once and preserve all user content.
        """
        import threading
        from shared.claude_md_manager import update_pact_routing

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "User-owned content line 1\n"
            "User-owned content line 2\n",
            encoding="utf-8",
        )

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        results: list[str | None] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=5)
                results.append(update_pact_routing())
            except BaseException as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == 2

        # Final file must contain exactly ONE canonical routing block
        final = claude_md.read_text(encoding="utf-8")
        assert final.count(
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
        ) == 1, (
            "Concurrent writes accumulated multiple routing blocks — lock "
            "failed to serialize"
        )
        assert final.count("<!-- PACT_ROUTING_END -->") == 1
        assert "User-owned content line 1" in final
        assert "User-owned content line 2" in final
        assert "# Project Memory" in final

    def test_concurrent_writes_preserve_session_start_block(
        self, tmp_path, monkeypatch
    ):
        """Regression: pre-fix failure mode — update_pact_routing racing
        with a SESSION_START block write could clobber the session info.

        We simulate the real session_init sequence: one thread writes a
        SESSION_START block, another thread runs update_pact_routing.
        With the lock in place, update_pact_routing's read sees the
        SESSION_START block and its write preserves it. Without the
        lock, the routing thread's stale read would drop the block
        on write.
        """
        import threading
        from shared.claude_md_manager import update_pact_routing

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "User content\n",
            encoding="utf-8",
        )

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        # Kick off update_pact_routing concurrently with another thread
        # that repeatedly writes a SESSION_START block. The lock must
        # serialize them so the final file contains both blocks.
        stop = threading.Event()
        errors: list[BaseException] = []

        def session_writer():
            """Simulates update_session_info by overwriting the file with
            a SESSION_START block plus whatever we read. This is the real
            failure mode: it must also go through the lock, but the pre-
            fix code did not. Here we exercise the lock on the routing
            side — the session writer is best-effort.
            """
            try:
                from shared.claude_md_manager import file_lock
                while not stop.is_set():
                    with file_lock(claude_md):
                        content = claude_md.read_text(encoding="utf-8")
                        if "<!-- SESSION_START -->" not in content:
                            new = content.replace(
                                "# Project Memory\n",
                                "# Project Memory\n\n<!-- SESSION_START -->\n"
                                "## Current Session\n"
                                "- Team: pact-abc123\n"
                                "<!-- SESSION_END -->\n",
                            )
                            claude_md.write_text(new, encoding="utf-8")
                    # Tiny yield so the routing thread gets a chance
                    import time as _time
                    _time.sleep(0.01)
            except BaseException as e:
                errors.append(e)

        def routing_worker():
            try:
                update_pact_routing()
            except BaseException as e:
                errors.append(e)

        sw = threading.Thread(target=session_writer)
        sw.start()
        # Give session_writer a moment to establish the SESSION_START block
        import time as _time
        _time.sleep(0.05)

        rw = threading.Thread(target=routing_worker)
        rw.start()
        rw.join(timeout=10)

        stop.set()
        sw.join(timeout=5)
        assert not errors, f"Thread errors: {errors}"

        final = claude_md.read_text(encoding="utf-8")
        # Both managed blocks must be present: routing preserved the
        # session block through its read-mutate-write cycle.
        assert "<!-- SESSION_START -->" in final, (
            "SESSION_START block was clobbered by concurrent "
            "update_pact_routing — lock did not serialize the critical "
            "section properly"
        )
        assert "<!-- SESSION_END -->" in final
        assert (
            "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
            in final
        )
        assert "<!-- PACT_ROUTING_END -->" in final
        assert "# Project Memory" in final
        assert "User content" in final

    def test_timeout_returns_fail_open_status(
        self, tmp_path, monkeypatch
    ):
        """When the lock cannot be acquired, update_pact_routing returns a
        'Failed to acquire lock ... Routing update skipped ...' status string.

        The 'failed' substring is load-bearing: session_init.py's routing
        check (`'failed' in msg.lower()`) uses it to send the message to
        system_messages (user-visible error surface) rather than silently
        into context_parts. A 5s lock acquisition failure is a genuine
        concurrency problem the user should see, not a silent fallback.
        """
        import threading
        from shared.claude_md_manager import update_pact_routing, file_lock
        from shared import claude_md_manager as cmm

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n\nUser content\n", encoding="utf-8"
        )

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

        result = update_pact_routing()

        assert result is not None
        # MUST contain "failed" — session_init routes on
        # `'failed' in msg.lower()` to system_messages for user visibility.
        assert "failed" in result.lower()
        assert "lock" in result.lower()
        assert "routing update skipped" in result.lower()

        holder_release.set()
        t.join(timeout=5)

    def test_remove_stale_kernel_block_timeout_returns_fail_open_status(
        self, mock_home
    ):
        """Companion test for remove_stale_kernel_block's timeout path.

        Same routing rationale as `test_timeout_returns_fail_open_status`:
        the 'failed' substring routes the message to system_messages for
        user visibility.
        """
        import threading
        from shared.claude_md_manager import remove_stale_kernel_block, file_lock
        from shared import claude_md_manager as cmm
        from unittest.mock import patch

        target = mock_home / ".claude" / "CLAUDE.md"
        target.write_text(
            "before\n"
            "<!-- PACT_START: pact -->\n"
            "kernel\n"
            "<!-- PACT_END -->\n"
            "after\n",
            encoding="utf-8",
        )

        holder_has_lock = threading.Event()
        holder_release = threading.Event()

        def holder():
            with file_lock(target):
                holder_has_lock.set()
                holder_release.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        assert holder_has_lock.wait(timeout=2)

        with patch.object(cmm, "_LOCK_TIMEOUT_SECONDS", 0.3):
            result = remove_stale_kernel_block()

        assert result is not None
        # MUST contain "failed" — session_init routes on
        # `'failed' in msg.lower()` to system_messages for user visibility.
        assert "failed" in result.lower()
        assert "lock" in result.lower()
        assert "kernel-block migration skipped" in result.lower()
        # File was NOT mutated (timeout is fail-open, write never happened)
        assert "PACT_START" in target.read_text(encoding="utf-8")

        holder_release.set()
        t.join(timeout=5)


# ---------------------------------------------------------------------------
# #404: CLAUDE.md restructuring — PACT_MANAGED and PACT_MEMORY boundaries
# ---------------------------------------------------------------------------
# These tests cover the three new pieces introduced by issue #404:
#
#   _build_migrated_content(content)  — pure function that transforms old-format
#       CLAUDE.md content into the new managed structure with PACT_MANAGED_START/
#       END and PACT_MEMORY_START/END boundaries.
#
#   migrate_to_managed_structure()  — integration wrapper that does file I/O,
#       file_lock, symlink guard, and idempotent check around _build_migrated_content.
#
#   ensure_project_memory_md() template update  — the template for new files now
#       includes the PACT_MANAGED and PACT_MEMORY markers.
#
# Risk tier: HIGH — migration runs on every existing project CLAUDE.md.

# Marker constants pinned locally so drift in the implementation is caught.
_MANAGED_START = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
_MANAGED_END = "<!-- PACT_MANAGED_END -->"
_MEMORY_START = "<!-- PACT_MEMORY_START -->"
_MEMORY_END = "<!-- PACT_MEMORY_END -->"
_ROUTING_START = "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
_ROUTING_END = "<!-- PACT_ROUTING_END -->"
_SESSION_START = "<!-- SESSION_START -->"
_SESSION_END = "<!-- SESSION_END -->"


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
        """Old '# Project Memory' is replaced by '# PACT Framework for Agentic Orchestration'."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert "# PACT Framework for Agentic Orchestration" in result
        # Old heading text should not survive as a top-level heading
        lines = result.splitlines()
        top_level_headings = [l for l in lines if l.startswith("# ") and not l.startswith("## ")]
        assert "# Project Memory" not in top_level_headings

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

    def test_routing_block_preserved(self):
        """The routing block (between its markers) must survive migration."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        assert _ROUTING_START in result
        assert _ROUTING_END in result
        assert "Some routing instructions here." in result

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
        """Markers must appear in the correct order: MANAGED_START -> ROUTING ->
        SESSION -> MEMORY_START -> memory sections -> MEMORY_END -> MANAGED_END.
        """
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        positions = {
            "managed_start": result.index(_MANAGED_START),
            "routing_start": result.index(_ROUTING_START),
            "routing_end": result.index(_ROUTING_END),
            "session_start": result.index(_SESSION_START),
            "session_end": result.index(_SESSION_END),
            "memory_start": result.index(_MEMORY_START),
            "memory_end": result.index(_MEMORY_END),
            "managed_end": result.index(_MANAGED_END),
        }

        assert positions["managed_start"] < positions["routing_start"]
        assert positions["routing_end"] < positions["session_start"]
        assert positions["session_end"] < positions["memory_start"]
        assert positions["memory_start"] < positions["memory_end"]
        assert positions["memory_end"] < positions["managed_end"]

    def test_new_memory_heading_inside_memory_boundary(self):
        """'# Project Memory (PACT-Managed)' must appear inside the memory boundary."""
        from shared.claude_md_manager import _build_migrated_content

        result = _build_migrated_content(self.CURRENT_FORMAT)

        mem_start_idx = result.index(_MEMORY_START)
        mem_end_idx = result.index(_MEMORY_END)
        memory_region = result[mem_start_idx:mem_end_idx]

        assert "# Project Memory (PACT-Managed)" in memory_region

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
        assert "# PACT Framework for Agentic Orchestration" in result

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
        """Flat text with no headings — everything should go to user content."""
        from shared.claude_md_manager import _build_migrated_content

        content = "Just some random text in a CLAUDE.md file.\nAnother line.\n"

        result = _build_migrated_content(content)

        assert _MANAGED_START in result
        assert _MANAGED_END in result
        managed_end_idx = result.index(_MANAGED_END)
        after_managed = result[managed_end_idx:]
        assert "Just some random text" in after_managed

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


class TestBuildMigratedContentIdempotent:
    """_build_migrated_content() does NOT have its own idempotency guard —
    that lives in migrate_to_managed_structure() which checks for
    MANAGED_START_MARKER before calling the pure function.

    Calling _build_migrated_content on already-migrated content WILL
    double-wrap. This is by design: the function is pure and stateless.
    The caller is responsible for the idempotency check.
    """

    def test_double_pass_produces_double_wrap(self):
        """Calling _build_migrated_content twice wraps content again —
        documenting that the caller must guard against re-migration.
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

        # Second pass double-wraps — this is expected, NOT a bug
        second_pass = _build_migrated_content(first_pass)

        assert second_pass.count(_MANAGED_START) == 2

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
        """Template heading is '# PACT Framework for Agentic Orchestration'."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        assert "# PACT Framework for Agentic Orchestration" in content

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
            "routing_start": content.index(_ROUTING_START),
            "session_start": content.index(_SESSION_START),
            "memory_start": content.index(_MEMORY_START),
            "memory_end": content.index(_MEMORY_END),
            "managed_end": content.index(_MANAGED_END),
        }

        assert positions["managed_start"] < positions["routing_start"]
        assert positions["routing_start"] < positions["session_start"]
        assert positions["session_start"] < positions["memory_start"]
        assert positions["memory_start"] < positions["memory_end"]
        assert positions["memory_end"] < positions["managed_end"]

    def test_created_file_has_pact_managed_memory_heading(self, tmp_path, monkeypatch):
        """Template includes '# Project Memory (PACT-Managed)' inside memory boundary."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        mem_start_idx = content.index(_MEMORY_START)
        mem_end_idx = content.index(_MEMORY_END)
        memory_region = content[mem_start_idx:mem_end_idx]
        assert "# Project Memory (PACT-Managed)" in memory_region


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
        with old kernel block markers that remove_stale_kernel_block() searches for.
        """
        from shared.claude_md_manager import MANAGED_START_MARKER
        assert "PACT_MANAGED_START" in MANAGED_START_MARKER
        assert "<!-- PACT_START" not in MANAGED_START_MARKER

    def test_marker_cardinality(self):
        """Exactly 4 new marker constants were added for #404."""
        from shared import claude_md_manager as cmm
        new_markers = [
            cmm.MANAGED_START_MARKER,
            cmm.MANAGED_END_MARKER,
            cmm.MEMORY_START_MARKER,
            cmm.MEMORY_END_MARKER,
        ]
        assert len(new_markers) == 4
        # All are unique
        assert len(set(new_markers)) == 4


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
