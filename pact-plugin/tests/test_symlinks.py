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


class TestConfigDirSymlinks:
    """C6 (#926): under a non-default CLAUDE_CONFIG_DIR, agents follow the config
    dir and the protocols symlink is created in BOTH roots (dual-location,
    answer-immune to the @-import ~ resolution question)."""

    @staticmethod
    def _plugin(tmp_path):
        plugin_root = tmp_path / "plugin"
        (plugin_root / "protocols").mkdir(parents=True)
        (plugin_root / "agents").mkdir(parents=True)
        (plugin_root / "agents" / "pact-secretary.md").write_text("x", encoding="utf-8")
        return plugin_root

    def test_protocols_dual_location_when_config_dir_differs(self, tmp_path, monkeypatch):
        from shared.symlinks import setup_plugin_symlinks
        plugin_root = self._plugin(tmp_path)
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        config_dir = tmp_path / "config-kimi"
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        monkeypatch.setattr(Path, "home", lambda: home)
        setup_plugin_symlinks()
        src = (plugin_root / "protocols").resolve()
        home_link = home / ".claude" / "protocols" / "pact-plugin"
        config_link = config_dir / "protocols" / "pact-plugin"
        assert home_link.is_symlink() and home_link.resolve() == src
        assert config_link.is_symlink() and config_link.resolve() == src

    def test_agents_follow_config_dir(self, tmp_path, monkeypatch):
        from shared.symlinks import setup_plugin_symlinks
        plugin_root = self._plugin(tmp_path)
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        config_dir = tmp_path / "config-kimi"
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        monkeypatch.setattr(Path, "home", lambda: home)
        setup_plugin_symlinks()
        # agents discovered from $CONFIG/agents — NOT $HOME/.claude/agents
        assert (config_dir / "agents" / "pact-secretary.md").is_symlink()
        assert not (home / ".claude" / "agents" / "pact-secretary.md").exists()

    def test_protocols_single_location_when_env_unset(self, tmp_path, monkeypatch):
        from shared.symlinks import setup_plugin_symlinks
        plugin_root = self._plugin(tmp_path)
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: home)
        setup_plugin_symlinks()
        # env unset → config root == $HOME/.claude → single create there
        assert (home / ".claude" / "protocols" / "pact-plugin").is_symlink()
        assert (home / ".claude" / "agents" / "pact-secretary.md").is_symlink()


class TestProtocolsMkdirFailOpen:
    """#926 (ea74cd0d): the protocols-symlink parent mkdir moved INSIDE the
    per-root try, so a pathological config_root whose mkdir raises must fail-open
    (a 'protocols failed' status), honoring setup_plugin_symlinks's
    'returns None/status, never raises' contract.

    NON-VACUITY: with the mkdir OUTSIDE the try (the pre-ea74cd0d shape) the
    OSError would PROPAGATE and setup_plugin_symlinks would RAISE -> this test
    (asserting no-raise + 'protocols failed') would ERROR. Verified: moving the
    mkdir above the try -> this test raises. Complements
    test_protocols_oserror_reports_failure, which covers the symlink_to (not
    mkdir) failure path.
    """

    def test_mkdir_failure_is_caught_and_reported(self, tmp_path, monkeypatch):
        from shared.symlinks import setup_plugin_symlinks

        plugin_root = tmp_path / "plugin"
        (plugin_root / "protocols").mkdir(parents=True)
        # No agents/ dir -> the (un-tried) agents mkdir block is skipped, so the
        # patched mkdir below only hits the protocols parent mkdir (in-try).
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        with patch.object(Path, "mkdir", side_effect=OSError("Read-only file system")):
            result = setup_plugin_symlinks()  # must NOT raise

        assert result is not None
        assert "protocols failed" in result
