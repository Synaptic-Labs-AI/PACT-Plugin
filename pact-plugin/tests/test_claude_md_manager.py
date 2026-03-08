"""
Tests for shared/claude_md_manager.py -- CLAUDE.md file manipulation.

Tests cover:
update_claude_md():
1. Returns None when CLAUDE_PLUGIN_ROOT doesn't exist
2. Returns None when source CLAUDE.md doesn't exist
3. Creates target CLAUDE.md when it doesn't exist
4. Updates existing PACT block between markers
5. Returns None when PACT block is already up to date
6. Warns about unmanaged PACT content
7. Appends PACT block when no markers and no conflict

ensure_project_memory_md():
8. Returns None when CLAUDE_PROJECT_DIR not set
9. Returns None when project CLAUDE.md already exists
10. Creates project CLAUDE.md with memory sections
11. Created file contains session markers
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
        """Should return None when plugin CLAUDE.md doesn't exist."""
        from shared.claude_md_manager import update_claude_md

        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        # No CLAUDE.md in plugin root

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))

        result = update_claude_md()

        assert result is None

    def test_creates_target_when_missing(self, tmp_path, monkeypatch):
        """Should create ~/.claude/CLAUDE.md when it doesn't exist."""
        from shared.claude_md_manager import update_claude_md

        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "CLAUDE.md").write_text("# PACT Orchestrator\nContent here")

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
        (plugin_root / "CLAUDE.md").write_text("# PACT v2\nNew content")

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
        (plugin_root / "CLAUDE.md").write_text(source_content)

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
        (plugin_root / "CLAUDE.md").write_text("# PACT content")

        claude_dir = tmp_path / "home" / ".claude"
        claude_dir.mkdir(parents=True)
        target = claude_dir / "CLAUDE.md"
        target.write_text("Manually installed PACT Orchestrator config")

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        result = update_claude_md()

        assert result is not None
        assert "unmanaged" in result

    def test_appends_when_no_markers_no_conflict(self, tmp_path, monkeypatch):
        """Should append PACT block when no markers and no PACT content."""
        from shared.claude_md_manager import update_claude_md

        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "CLAUDE.md").write_text("# PACT Setup")

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
        """Should create project CLAUDE.md with memory sections."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = ensure_project_memory_md()

        assert result == "Created project CLAUDE.md with memory sections"
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "# Project Memory" in content
        assert "## Retrieved Context" in content
        assert "## Working Memory" in content

    def test_created_file_contains_session_markers(self, tmp_path, monkeypatch):
        """Should include session markers in the created file."""
        from shared.claude_md_manager import ensure_project_memory_md

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ensure_project_memory_md()

        content = (tmp_path / "CLAUDE.md").read_text()
        assert "<!-- SESSION_START -->" in content
        assert "<!-- SESSION_END -->" in content


class TestUpdateClaudeMdErrorPaths:
    """Tests for update_claude_md() exception handling."""

    def test_returns_error_message_on_read_failure(self, tmp_path, monkeypatch):
        """Should return truncated error message when source file read fails."""
        from shared.claude_md_manager import update_claude_md

        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        source = plugin_root / "CLAUDE.md"
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
