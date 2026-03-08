"""
Tests for shared/symlinks.py -- plugin symlink management.

Tests cover:
setup_plugin_symlinks():
1. Returns None when CLAUDE_PLUGIN_ROOT doesn't exist
2. Creates protocols symlink when missing
3. Updates protocols symlink when pointing to wrong target
4. Skips protocols symlink when already correct
5. Creates agent file symlinks
6. Updates agent symlinks when pointing to wrong target
7. Skips existing real agent files (user override)
8. Returns "PACT symlinks verified" when all links already correct
9. Handles OSError during protocol symlink creation
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestSetupPluginSymlinks:
    """Tests for setup_plugin_symlinks() -- symlink creation."""

    def test_returns_none_when_plugin_root_missing(self, monkeypatch):
        """Should return None when CLAUDE_PLUGIN_ROOT doesn't exist."""
        from shared.symlinks import setup_plugin_symlinks

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/nonexistent/path")

        result = setup_plugin_symlinks()

        assert result is None

    def test_creates_protocols_symlink(self, tmp_path, monkeypatch):
        """Should create protocols symlink when it doesn't exist."""
        from shared.symlinks import setup_plugin_symlinks

        # Set up plugin root with protocols dir
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "protocols").mkdir()

        # Set up claude dir without symlink
        claude_dir = tmp_path / "home" / ".claude"
        claude_dir.mkdir(parents=True)

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        result = setup_plugin_symlinks()

        assert result is not None
        assert "protocols linked" in result
        protocols_link = claude_dir / "protocols" / "pact-plugin"
        assert protocols_link.is_symlink()
        assert protocols_link.resolve() == (plugin_root / "protocols").resolve()

    def test_updates_protocols_symlink_when_wrong_target(self, tmp_path, monkeypatch):
        """Should update protocols symlink when pointing to wrong location."""
        from shared.symlinks import setup_plugin_symlinks

        # Set up plugin root with protocols dir
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "protocols").mkdir()

        # Set up claude dir with existing wrong symlink
        claude_dir = tmp_path / "home" / ".claude"
        protocols_dir = claude_dir / "protocols"
        protocols_dir.mkdir(parents=True)
        old_target = tmp_path / "old_protocols"
        old_target.mkdir()
        protocols_link = protocols_dir / "pact-plugin"
        protocols_link.symlink_to(old_target)

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        result = setup_plugin_symlinks()

        assert result is not None
        assert "protocols updated" in result
        assert protocols_link.resolve() == (plugin_root / "protocols").resolve()

    def test_skips_correct_protocols_symlink(self, tmp_path, monkeypatch):
        """Should not update protocols symlink when already correct."""
        from shared.symlinks import setup_plugin_symlinks

        # Set up plugin root with protocols dir
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "protocols").mkdir()

        # Set up claude dir with correct symlink
        claude_dir = tmp_path / "home" / ".claude"
        protocols_dir = claude_dir / "protocols"
        protocols_dir.mkdir(parents=True)
        protocols_link = protocols_dir / "pact-plugin"
        protocols_link.symlink_to(plugin_root / "protocols")

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        result = setup_plugin_symlinks()

        # No agents dir, so only protocols matters -- should be "verified"
        assert result == "PACT symlinks verified"

    def test_creates_agent_symlinks(self, tmp_path, monkeypatch):
        """Should create symlinks for agent files."""
        from shared.symlinks import setup_plugin_symlinks

        # Set up plugin root with agents
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        agents_dir = plugin_root / "agents"
        agents_dir.mkdir()
        (agents_dir / "pact-backend-coder.md").write_text("agent def")
        (agents_dir / "pact-frontend-coder.md").write_text("agent def")

        # Set up claude dir
        claude_dir = tmp_path / "home" / ".claude"
        claude_dir.mkdir(parents=True)

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        result = setup_plugin_symlinks()

        assert result is not None
        assert "2 agents linked" in result
        agents_dst = claude_dir / "agents"
        assert (agents_dst / "pact-backend-coder.md").is_symlink()
        assert (agents_dst / "pact-frontend-coder.md").is_symlink()

    def test_returns_verified_when_all_correct(self, tmp_path, monkeypatch):
        """Should return 'PACT symlinks verified' when everything is up to date."""
        from shared.symlinks import setup_plugin_symlinks

        # Set up plugin root with protocols and agents
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "protocols").mkdir()
        agents_dir = plugin_root / "agents"
        agents_dir.mkdir()
        (agents_dir / "pact-test.md").write_text("agent def")

        # Set up claude dir with correct symlinks
        claude_dir = tmp_path / "home" / ".claude"
        protocols_dir = claude_dir / "protocols"
        protocols_dir.mkdir(parents=True)
        (protocols_dir / "pact-plugin").symlink_to(plugin_root / "protocols")
        agents_dst = claude_dir / "agents"
        agents_dst.mkdir()
        (agents_dst / "pact-test.md").symlink_to(agents_dir / "pact-test.md")

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        result = setup_plugin_symlinks()

        assert result == "PACT symlinks verified"

    def test_skips_real_agent_files(self, tmp_path, monkeypatch):
        """Should skip agent files that are real files (user override)."""
        from shared.symlinks import setup_plugin_symlinks

        # Set up plugin root with agents
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        agents_dir = plugin_root / "agents"
        agents_dir.mkdir()
        (agents_dir / "pact-custom.md").write_text("plugin agent def")

        # Set up claude dir with real file (not symlink)
        claude_dir = tmp_path / "home" / ".claude"
        agents_dst = claude_dir / "agents"
        agents_dst.mkdir(parents=True)
        (agents_dst / "pact-custom.md").write_text("user override")

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        result = setup_plugin_symlinks()

        # Real file should not be replaced
        assert not (agents_dst / "pact-custom.md").is_symlink()
        assert (agents_dst / "pact-custom.md").read_text() == "user override"

    def test_protocols_oserror_reports_failure(self, tmp_path, monkeypatch):
        """Should include 'failed' in result when protocol symlink creation raises OSError."""
        from shared.symlinks import setup_plugin_symlinks

        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        (plugin_root / "protocols").mkdir()

        claude_dir = tmp_path / "home" / ".claude"
        protocols_dir = claude_dir / "protocols"
        protocols_dir.mkdir(parents=True)

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        # Make symlink_to raise OSError
        with patch.object(Path, "symlink_to", side_effect=OSError("Permission denied")):
            result = setup_plugin_symlinks()

        assert result is not None
        assert "protocols failed" in result
