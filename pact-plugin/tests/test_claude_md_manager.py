"""
Tests for shared/claude_md_manager.py -- CLAUDE.md file manipulation.

Tests cover:
update_claude_md():
1. Returns None when CLAUDE_PLUGIN_ROOT doesn't exist
2. Returns None when source CLAUDE-kernel.md doesn't exist
3. Creates target CLAUDE.md when it doesn't exist
4. Updates existing PACT block between markers
5. Returns None when PACT block is already up to date
6. Warns about unmanaged PACT content
7. Appends PACT block when no markers and no conflict

ensure_project_memory_md():
8. Returns None when CLAUDE_PROJECT_DIR not set
9. Returns None when project CLAUDE.md already exists (legacy ./CLAUDE.md)
10. Creates project CLAUDE.md (.claude/CLAUDE.md, new default) with memory sections
11. Created file contains session markers
12. Returns None when .claude/CLAUDE.md already exists (no overwrite)
13. Returns None when only legacy ./CLAUDE.md exists (no migration)
14. .claude/CLAUDE.md takes precedence when both locations exist
15. Created .claude/CLAUDE.md has 0o600 permissions; .claude/ dir 0o700
"""

import sys
from pathlib import Path

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestUpdateClaudeMd:
    """Tests for update_claude_md() -- global CLAUDE.md management."""

    def test_returns_none_when_plugin_root_missing(self, monkeypatch):
        """Should return None when CLAUDE_PLUGIN_ROOT doesn't exist."""
        from shared.claude_md_manager import update_claude_md

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/nonexistent/path")

        result = update_claude_md()

        assert result is None

    def test_returns_none_when_source_missing(self, tmp_path, monkeypatch):
        """Should return None when plugin CLAUDE-kernel.md doesn't exist."""
        from shared.claude_md_manager import update_claude_md

        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        # No CLAUDE-kernel.md in plugin root

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))

        result = update_claude_md()

        assert result is None

    def test_creates_target_when_missing(self, tmp_path, monkeypatch):
        """Should create ~/.claude/CLAUDE.md when it doesn't exist."""
        from shared.claude_md_manager import update_claude_md

        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "CLAUDE-kernel.md").write_text("# PACT Orchestrator\nContent here")

        claude_dir = tmp_path / "home" / ".claude"
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        result = update_claude_md()

        assert result == "Created CLAUDE.md with PACT Orchestrator"
        target = claude_dir / "CLAUDE.md"
        assert target.exists()
        content = target.read_text()
        assert "PACT_START" in content
        assert "PACT_END" in content
        assert "# PACT Orchestrator" in content

    def test_updates_existing_pact_block(self, tmp_path, monkeypatch):
        """Should replace content between markers when markers exist."""
        from shared.claude_md_manager import update_claude_md

        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "CLAUDE-kernel.md").write_text("# PACT v2\nNew content")

        claude_dir = tmp_path / "home" / ".claude"
        claude_dir.mkdir(parents=True)
        target = claude_dir / "CLAUDE.md"
        target.write_text(
            "User stuff\n"
            "<!-- PACT_START: Managed by pact-plugin - Do not edit this block -->\n"
            "# PACT v1\nOld content\n"
            "<!-- PACT_END -->\n"
            "More user stuff"
        )

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        result = update_claude_md()

        assert result == "PACT Orchestrator updated"
        content = target.read_text()
        assert "# PACT v2" in content
        assert "New content" in content
        assert "# PACT v1" not in content
        assert "User stuff" in content
        assert "More user stuff" in content

    def test_returns_none_when_already_up_to_date(self, tmp_path, monkeypatch):
        """Should return None when PACT block matches source."""
        from shared.claude_md_manager import update_claude_md

        source_content = "# PACT\nSame content"
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "CLAUDE-kernel.md").write_text(source_content)

        start = "<!-- PACT_START: Managed by pact-plugin - Do not edit this block -->"
        end = "<!-- PACT_END -->"

        claude_dir = tmp_path / "home" / ".claude"
        claude_dir.mkdir(parents=True)
        target = claude_dir / "CLAUDE.md"
        target.write_text(f"{start}\n{source_content}\n{end}")

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        result = update_claude_md()

        assert result is None

    def test_warns_about_unmanaged_pact(self, tmp_path, monkeypatch):
        """Should warn when PACT Orchestrator found without markers."""
        from shared.claude_md_manager import update_claude_md

        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "CLAUDE-kernel.md").write_text("# PACT content")

        claude_dir = tmp_path / "home" / ".claude"
        claude_dir.mkdir(parents=True)
        target = claude_dir / "CLAUDE.md"
        target.write_text("Manually installed PACT Orchestrator config")

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        result = update_claude_md()

        assert result is not None
        assert "unmanaged" in result

    def test_handles_multiple_pact_start_markers_gracefully(
        self, tmp_path, monkeypatch, capsys
    ):
        """Corrupted CLAUDE.md with multiple PACT_START markers must not silently
        drop intermediate content. The manager should warn to stderr, replace
        only the LAST PACT block, and preserve everything before it verbatim.

        Regression: prior implementation used `split(START_MARKER)` and only
        kept parts[0] and parts[1], silently dropping parts[2:] (any user
        content between the second start marker and the trailing end marker).
        """
        from shared.claude_md_manager import update_claude_md

        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "CLAUDE-kernel.md").write_text("# PACT v2\nNew content")

        start = "<!-- PACT_START: Managed by pact-plugin - Do not edit this block -->"
        end = "<!-- PACT_END -->"

        # Construct a corrupted CLAUDE.md with THREE PACT_START markers.
        # The intermediate "stale orphan block" + "user notes" between blocks
        # must survive the update; only the final block should be rewritten.
        corrupted = (
            "User preamble\n"
            f"{start}\n"
            "# Stale PACT v0\nFirst orphan body\n"
            f"{end}\n"
            "Important user notes between blocks\n"
            f"{start}\n"
            "# Stale PACT v0.5\nSecond orphan body\n"
            f"{end}\n"
            "More user notes\n"
            f"{start}\n"
            "# PACT v1\nMost recent body\n"
            f"{end}\n"
            "User trailing content"
        )

        claude_dir = tmp_path / "home" / ".claude"
        claude_dir.mkdir(parents=True)
        target = claude_dir / "CLAUDE.md"
        target.write_text(corrupted)

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        result = update_claude_md()

        assert result == "PACT Orchestrator updated"

        content = target.read_text()

        # New content replaced the LAST block
        assert "# PACT v2" in content
        assert "New content" in content
        # The most recent stale body must be gone (it was the one replaced)
        assert "Most recent body" not in content
        # All surrounding user content must survive verbatim
        assert "User preamble" in content
        assert "Important user notes between blocks" in content
        assert "More user notes" in content
        assert "User trailing content" in content
        # Intermediate orphan blocks are preserved (not silently dropped)
        assert "# Stale PACT v0" in content
        assert "First orphan body" in content
        assert "# Stale PACT v0.5" in content
        assert "Second orphan body" in content

        # A warning was emitted to stderr identifying the corruption
        captured = capsys.readouterr()
        assert "PACT warning" in captured.err
        assert "3 PACT_START markers" in captured.err

    def test_appends_when_no_markers_no_conflict(self, tmp_path, monkeypatch):
        """Should append PACT block when no markers and no PACT content."""
        from shared.claude_md_manager import update_claude_md

        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "CLAUDE-kernel.md").write_text("# PACT Setup")

        claude_dir = tmp_path / "home" / ".claude"
        claude_dir.mkdir(parents=True)
        target = claude_dir / "CLAUDE.md"
        target.write_text("# My Config\nSome settings\n")

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        result = update_claude_md()

        assert result == "PACT Orchestrator added to CLAUDE.md"
        content = target.read_text()
        assert "# My Config" in content
        assert "PACT_START" in content
        assert "# PACT Setup" in content


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


class TestUpdateClaudeMdErrorPaths:
    """Tests for update_claude_md() exception handling."""

    def test_returns_error_message_on_read_failure(self, tmp_path, monkeypatch):
        """Should return truncated error message when source file read fails."""
        from shared.claude_md_manager import update_claude_md

        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        source = plugin_root / "CLAUDE-kernel.md"
        source.write_text("content")

        claude_dir = tmp_path / "home" / ".claude"
        claude_dir.mkdir(parents=True)
        target = claude_dir / "CLAUDE.md"
        target.write_text("existing")

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        # Make target unreadable to trigger exception in read_text
        from unittest.mock import patch
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            result = update_claude_md()

        assert result is not None
        assert "PACT update failed:" in result

    def test_returns_none_when_plugin_root_env_empty(self, monkeypatch):
        """Should return None when CLAUDE_PLUGIN_ROOT is empty string."""
        from shared.claude_md_manager import update_claude_md

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "")

        result = update_claude_md()

        assert result is None


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
