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
