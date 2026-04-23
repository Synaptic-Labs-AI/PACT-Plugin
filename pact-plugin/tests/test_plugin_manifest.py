"""
Tests for shared/plugin_manifest.py — total-function banner helper
(#500 teammate plugin-version visibility).

Smoke coverage per architect fire-matrix (§6):
  - Happy path (row 1)
  - CLAUDE_PLUGIN_ROOT unset (row 2) — critical fail-open cell
  - plugin.json missing (row 4)  — critical fail-open cell
  - plugin.json malformed JSON (row 5) — critical fail-open cell
  - Invariant checks (never raises, non-empty, single-line, prefix)

Comprehensive row coverage (name/version schema shapes, OSError,
UnicodeDecodeError, newline sanitization, etc.) is TEST phase work.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


def _make_plugin_root(tmp_path: Path, manifest: str | None) -> Path:
    """Create a fake plugin root. If manifest is None, .claude-plugin/
    is absent. If "", the dir exists but plugin.json is absent. Otherwise
    plugin.json is written with the given raw text.
    """
    root = tmp_path / "plugin"
    if manifest is None:
        root.mkdir()
        return root
    claude_plugin = root / ".claude-plugin"
    claude_plugin.mkdir(parents=True)
    if manifest != "":
        (claude_plugin / "plugin.json").write_text(manifest, encoding="utf-8")
    return root


class TestFormatPluginBanner:
    """Smoke tests for format_plugin_banner()."""

    def test_happy_path(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path,
            json.dumps({"name": "PACT", "version": "3.18.1"}),
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: PACT 3.18.1 (root: {root})"

    def test_env_unset(self, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)

        banner = format_plugin_banner()

        assert banner == "PACT plugin: unknown (root: <unset>)"

    def test_env_empty_string(self, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "   ")

        banner = format_plugin_banner()

        assert banner == "PACT plugin: unknown (root: <unset>)"

    def test_plugin_json_missing(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(tmp_path, None)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_plugin_json_malformed(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(tmp_path, "{not valid json")
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"


class TestBannerInvariants:
    """Total-function invariants — must hold across every input."""

    @pytest.mark.parametrize(
        "manifest_text",
        [
            None,  # plugin.json missing
            "",  # .claude-plugin dir exists but file absent
            "{not valid json",
            json.dumps({"name": "PACT", "version": "3.18.1"}),
            json.dumps({"name": "", "version": "3.18.1"}),
            json.dumps({"name": "PACT"}),  # missing version
            json.dumps({"version": "3.18.1"}),  # missing name
            json.dumps({"name": "PACT", "version": 3}),  # non-string version
            json.dumps(["not", "a", "dict"]),
            json.dumps({"name": "PACT\n", "version": "3.18.1\r"}),
        ],
    )
    def test_invariants_hold(self, tmp_path, monkeypatch, manifest_text):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(tmp_path, manifest_text)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert isinstance(banner, str)
        assert banner  # non-empty
        assert banner.startswith("PACT plugin: ")
        assert "\n" not in banner
        assert "\r" not in banner

    def test_newlines_stripped_from_happy_path(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path,
            json.dumps({"name": "PACT\n", "version": "3.18\r.1"}),
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert "\n" not in banner
        assert "\r" not in banner
        assert "PACT " in banner  # name retained (with trailing space)
        assert "3.18 .1" in banner  # embedded \r replaced with space
