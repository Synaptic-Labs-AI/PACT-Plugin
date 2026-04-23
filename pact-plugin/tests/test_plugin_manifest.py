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


class TestFailOpenFireMatrixExtendedRows:
    """Architect fire-matrix §6 — explicit named rows beyond smoke coverage.

    Smoke covers rows 1, 2, 4, 5 (+ a parametrized sweep of 6-11). These
    named tests pin each remaining row individually so a failure in any one
    cell surfaces a specific diagnostic rather than a collapsed parametrize
    failure. The prefix + root-display pattern is the public contract;
    asserting the exact sentinel literal guards against any caller
    ever seeing a partial or mangled banner under failure.
    """

    def test_row3_env_set_but_root_does_not_exist(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        nonexistent = tmp_path / "does-not-exist"
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(nonexistent))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {nonexistent})"

    def test_row7_missing_version_key(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(tmp_path, json.dumps({"name": "PACT"}))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_row8_missing_name_key(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(tmp_path, json.dumps({"version": "3.18.1"}))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_row9_version_is_integer(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path, json.dumps({"name": "PACT", "version": 3})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_row10a_name_empty_string(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path, json.dumps({"name": "", "version": "3.18.1"})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_row10b_version_empty_string(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path, json.dumps({"name": "PACT", "version": ""})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_row6a_top_level_is_list(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(tmp_path, json.dumps(["not", "a", "dict"]))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_row6b_top_level_is_string(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(tmp_path, json.dumps("just-a-string"))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_row12_permission_error_on_read(self, tmp_path, monkeypatch):
        """plugin.json exists but cannot be read (chmod 000 / EACCES).

        On platforms where chmod 000 does not actually block root, the test
        still passes because the production helper treats any OSError
        subclass uniformly. We monkeypatch Path.read_text to guarantee the
        EACCES is reliably raised, which decouples the test from macOS
        permission quirks.
        """
        from pathlib import Path as _Path

        from shared import plugin_manifest

        root = _make_plugin_root(
            tmp_path, json.dumps({"name": "PACT", "version": "3.18.1"})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        original_read_text = _Path.read_text

        def read_text_raising_permission(self, *args, **kwargs):
            if self.name == "plugin.json":
                raise PermissionError("EACCES simulated")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(_Path, "read_text", read_text_raising_permission)

        banner = plugin_manifest.format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_row12b_generic_oserror_on_read(self, tmp_path, monkeypatch):
        """Any OSError subclass on read → fail-open sentinel."""
        from pathlib import Path as _Path

        from shared import plugin_manifest

        root = _make_plugin_root(
            tmp_path, json.dumps({"name": "PACT", "version": "3.18.1"})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        def read_text_raising(self, *args, **kwargs):
            raise OSError("ELOOP / symlink loop / disk IO error")

        monkeypatch.setattr(_Path, "read_text", read_text_raising)

        banner = plugin_manifest.format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_row13_unicode_decode_error(self, tmp_path, monkeypatch):
        """plugin.json contains non-utf-8 bytes → fail-open sentinel."""
        from shared.plugin_manifest import format_plugin_banner

        root = tmp_path / "plugin"
        claude_plugin = root / ".claude-plugin"
        claude_plugin.mkdir(parents=True)
        # Write raw non-utf-8 bytes (invalid continuation byte).
        (claude_plugin / "plugin.json").write_bytes(b"\xff\xfe\x00non-utf8")
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_empty_dict_manifest(self, tmp_path, monkeypatch):
        """`{}` — valid JSON, valid dict, but no name/version."""
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(tmp_path, json.dumps({}))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_extra_junk_fields_ignored_happy_path(self, tmp_path, monkeypatch):
        """Manifest with extra fields (description, repository, etc.) →
        banner still succeeds, surfaces only name + version."""
        from shared.plugin_manifest import format_plugin_banner

        manifest = {
            "name": "PACT",
            "version": "3.18.1",
            "description": "Prepare, Architect, Code, Test framework",
            "repository": "https://example.com/repo",
            "author": "someone",
            "unexpected_future_field": [1, 2, 3],
        }
        root = _make_plugin_root(tmp_path, json.dumps(manifest))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: PACT 3.18.1 (root: {root})"

    def test_root_is_a_file_not_directory(self, tmp_path, monkeypatch):
        """CLAUDE_PLUGIN_ROOT points at a regular file → fail-open."""
        from shared.plugin_manifest import format_plugin_banner

        root_as_file = tmp_path / "not-a-directory"
        root_as_file.write_text("I am a file")
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root_as_file))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root_as_file})"

    def test_plugin_json_is_a_directory(self, tmp_path, monkeypatch):
        """plugin.json entry exists but is a directory, not a file →
        fail-open (read_text raises IsADirectoryError, a subclass of
        OSError)."""
        from shared.plugin_manifest import format_plugin_banner

        root = tmp_path / "plugin"
        claude_plugin = root / ".claude-plugin"
        (claude_plugin / "plugin.json").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"


class TestTotalFunctionOuterGuard:
    """The outer blanket try/except in format_plugin_banner() is the last
    line of defense against any future regression in the helper chain.
    These tests pin that guard: inject an unexpected exception type
    (TypeError, RuntimeError) into the helpers and verify the outer
    except still produces the safe fallback sentinel."""

    def test_unexpected_typeerror_inside_resolve_root_is_caught(
        self, monkeypatch
    ):
        from shared import plugin_manifest

        def explode():
            raise TypeError("simulated upstream regression")

        monkeypatch.setattr(
            plugin_manifest, "_resolve_plugin_root", explode
        )

        banner = plugin_manifest.format_plugin_banner()

        # Outer except loses the path, so sentinel uses <unset>.
        assert banner == "PACT plugin: unknown (root: <unset>)"

    def test_unexpected_runtimeerror_inside_read_manifest_is_caught(
        self, tmp_path, monkeypatch
    ):
        from shared import plugin_manifest

        root = _make_plugin_root(
            tmp_path, json.dumps({"name": "PACT", "version": "3.18.1"})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        def explode(plugin_root):
            raise RuntimeError("simulated regression in _read_manifest")

        monkeypatch.setattr(plugin_manifest, "_read_manifest", explode)

        banner = plugin_manifest.format_plugin_banner()

        # Reached via the outer except, which uses <unset> (not the
        # resolved root). This is the documented worst-case fallback.
        assert banner == "PACT plugin: unknown (root: <unset>)"

    def test_totality_under_many_exception_types(self, monkeypatch):
        """The outer except must catch every non-BaseException subclass.

        Exercises a broader set of exception types than smoke covered.
        Each one should fall through to the "<unset>" sentinel, never
        propagate, never return None, never return empty string.
        """
        from shared import plugin_manifest

        exc_types = [
            ValueError("v"),
            TypeError("t"),
            RuntimeError("r"),
            KeyError("k"),
            AttributeError("a"),
            LookupError("l"),
            ArithmeticError("arith"),
        ]

        for exc in exc_types:
            def explode(_exc=exc):
                raise _exc

            monkeypatch.setattr(
                plugin_manifest, "_resolve_plugin_root", explode
            )
            banner = plugin_manifest.format_plugin_banner()
            assert banner == "PACT plugin: unknown (root: <unset>)"
            assert isinstance(banner, str)
            assert banner
            assert "\n" not in banner


class TestBannerContractInvariants:
    """Banner contract invariants pinned explicitly beyond the smoke
    parametrize: prefix, single-line, non-empty, no CRLF of any form.

    These tests exist to make the public contract load-bearing — a
    future caller (session_init, peer_inject, or a new hook) that
    tokenizes or splits on banner output can rely on these guarantees."""

    @pytest.mark.parametrize(
        "env_state,manifest_text",
        [
            ("unset", None),
            ("set-empty-dir", None),
            ("set-valid", json.dumps({"name": "PACT", "version": "3.18.1"})),
            ("set-malformed", "not json at all {"),
            ("set-junk-name", json.dumps({"name": 42, "version": "3.18.1"})),
            ("set-crlf-values", json.dumps(
                {"name": "P\r\nA\rC\nT", "version": "3\n.18\r.1"}
            )),
        ],
    )
    def test_prefix_always_pact_plugin_space(
        self, tmp_path, monkeypatch, env_state, manifest_text
    ):
        from shared.plugin_manifest import format_plugin_banner

        if env_state == "unset":
            monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        else:
            root = _make_plugin_root(tmp_path, manifest_text)
            monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner.startswith("PACT plugin: ")

    def test_banner_contains_no_crlf_under_hostile_manifest(
        self, tmp_path, monkeypatch
    ):
        """A hostile plugin.json with CRLF in both fields must not leak
        line-breaks into the banner. Exhaustive CRLF combinations."""
        from shared.plugin_manifest import format_plugin_banner

        hostile_values = [
            ("P\nACT", "3.18.1"),
            ("PACT", "3.18.1\n"),
            ("PA\r\nCT", "3.\n18.\r1"),
            ("\n\nPACT", "\r\n3.18.1\r\n"),
        ]
        for name, version in hostile_values:
            root = _make_plugin_root(
                tmp_path / f"r-{hash((name, version)) & 0xFFFF}",
                json.dumps({"name": name, "version": version}),
            )
            monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

            banner = format_plugin_banner()

            assert "\n" not in banner, banner
            assert "\r" not in banner, banner
            assert banner.startswith("PACT plugin: ")
            assert banner  # non-empty

    def test_banner_is_pure_function_of_env_and_manifest(
        self, tmp_path, monkeypatch
    ):
        """Idempotency: two calls with the same inputs return equal strings."""
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path, json.dumps({"name": "PACT", "version": "3.18.1"})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        b1 = format_plugin_banner()
        b2 = format_plugin_banner()

        assert b1 == b2


class TestSessionInitSlotAIntegration:
    """End-to-end: banner appears in session_init.main() additionalContext.

    Verifies wiring between the helper and the hook — not just that the
    helper produces a string, but that session_init.main() actually calls
    it and includes the result in the emitted additionalContext. Mirrors
    the patching style of TestTeamResumeDetection in test_session_init.py.
    """

    def _run_main(self, monkeypatch, tmp_path, plugin_root=None, manifest=None):
        import io as _io
        from unittest.mock import patch as _patch

        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "project"))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        (tmp_path / "home").mkdir(exist_ok=True)

        if plugin_root is not None:
            if manifest is not None:
                claude_plugin = plugin_root / ".claude-plugin"
                claude_plugin.mkdir(parents=True)
                (claude_plugin / "plugin.json").write_text(manifest)
            monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        else:
            monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)

        stdin_data = json.dumps(
            {"session_id": "abc12345-0000-0000-0000-000000000000"}
        )

        with _patch("session_init.setup_plugin_symlinks", return_value=None), \
             _patch("session_init.remove_stale_kernel_block", return_value=None), \
             _patch("session_init.update_pact_routing", return_value=None), \
             _patch("session_init.ensure_project_memory_md", return_value=None), \
             _patch("session_init.check_pinned_staleness", return_value=None), \
             _patch("session_init.update_session_info", return_value=None), \
             _patch("session_init.get_task_list", return_value=None), \
             _patch("session_init.restore_last_session", return_value=None), \
             _patch("session_init.check_paused_state", return_value=None), \
             _patch("sys.stdin", _io.StringIO(stdin_data)), \
             _patch("sys.stdout", new_callable=_io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        return output["hookSpecificOutput"]["additionalContext"]

    def test_banner_appears_in_additional_context_happy_path(
        self, monkeypatch, tmp_path
    ):
        plugin_root = tmp_path / "installed-cache"
        additional = self._run_main(
            monkeypatch,
            tmp_path,
            plugin_root=plugin_root,
            manifest=json.dumps({"name": "PACT", "version": "3.18.1"}),
        )

        assert f"PACT plugin: PACT 3.18.1 (root: {plugin_root})" in additional

    def test_banner_appears_even_when_plugin_root_unset(
        self, monkeypatch, tmp_path
    ):
        additional = self._run_main(monkeypatch, tmp_path, plugin_root=None)

        assert "PACT plugin: unknown (root: <unset>)" in additional

    def test_banner_appears_when_plugin_json_malformed(
        self, monkeypatch, tmp_path
    ):
        plugin_root = tmp_path / "installed-cache"
        additional = self._run_main(
            monkeypatch,
            tmp_path,
            plugin_root=plugin_root,
            manifest="{this is not json",
        )

        assert f"PACT plugin: unknown (root: {plugin_root})" in additional

    def test_banner_follows_stale_block_directive_in_join_order(
        self, monkeypatch, tmp_path
    ):
        """Slot A placement: banner comes after pin/stale-block diagnostics
        in the `" | ".join(context_parts)` output, per architecture §4.
        Verified by asserting the banner is not the very first token
        in additionalContext (team prelude inserts at index 0, then
        bootstrap + diagnostics precede the banner)."""
        plugin_root = tmp_path / "installed-cache"
        additional = self._run_main(
            monkeypatch,
            tmp_path,
            plugin_root=plugin_root,
            manifest=json.dumps({"name": "PACT", "version": "3.18.1"}),
        )

        banner = f"PACT plugin: PACT 3.18.1 (root: {plugin_root})"
        assert banner in additional
        # Banner is not the very first content — team prelude + bootstrap
        # directive are emitted first (insert(0, ...)).
        assert not additional.startswith(banner)


class TestPeerInjectIntegration:
    """End-to-end: banner appears in peer_inject.get_peer_context() return
    between peer_context and _TEACHBACK_REMINDER, per architecture §3.3."""

    def _write_team_config(self, tmp_path, members):
        team_dir = tmp_path / "teams" / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"members": members})
        )
        return tmp_path / "teams"

    def test_banner_appears_in_peer_context_with_multiple_members(
        self, tmp_path, monkeypatch
    ):
        from peer_inject import _TEACHBACK_REMINDER, get_peer_context

        plugin_root = tmp_path / "installed-cache"
        claude_plugin = plugin_root / ".claude-plugin"
        claude_plugin.mkdir(parents=True)
        (claude_plugin / "plugin.json").write_text(
            json.dumps({"name": "PACT", "version": "3.18.1"})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))

        teams_dir = self._write_team_config(
            tmp_path,
            [
                {"name": "architect", "agentType": "pact-architect"},
                {"name": "backend-coder", "agentType": "pact-backend-coder"},
            ],
        )

        result = get_peer_context(
            agent_type="pact-architect",
            team_name="pact-test",
            agent_name="architect",
            teams_dir=str(teams_dir),
        )

        assert result is not None
        banner = f"PACT plugin: PACT 3.18.1 (root: {plugin_root})"
        assert banner in result
        # Banner is BETWEEN peer_context and _TEACHBACK_REMINDER.
        banner_idx = result.index(banner)
        reminder_idx = result.index(_TEACHBACK_REMINDER)
        assert banner_idx < reminder_idx, (
            "banner must precede the teachback reminder"
        )
        # peer_context text appears before the banner.
        assert result.index("backend-coder") < banner_idx

    def test_banner_appears_when_alone_on_team(self, tmp_path, monkeypatch):
        from peer_inject import _TEACHBACK_REMINDER, get_peer_context

        plugin_root = tmp_path / "installed-cache"
        claude_plugin = plugin_root / ".claude-plugin"
        claude_plugin.mkdir(parents=True)
        (claude_plugin / "plugin.json").write_text(
            json.dumps({"name": "PACT", "version": "3.18.1"})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))

        teams_dir = self._write_team_config(
            tmp_path,
            [{"name": "architect", "agentType": "pact-architect"}],
        )

        result = get_peer_context(
            agent_type="pact-architect",
            team_name="pact-test",
            agent_name="architect",
            teams_dir=str(teams_dir),
        )

        assert result is not None
        assert "only active teammate" in result.lower()
        banner = f"PACT plugin: PACT 3.18.1 (root: {plugin_root})"
        assert banner in result
        assert result.index(banner) < result.index(_TEACHBACK_REMINDER)

    def test_banner_appears_on_failure_sentinel_in_peer_context(
        self, tmp_path, monkeypatch
    ):
        """Even when plugin.json fails to read, the sentinel banner still
        appears in the peer_context output — fail-open at the integration
        layer, not just the helper layer."""
        from peer_inject import get_peer_context

        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)

        teams_dir = self._write_team_config(
            tmp_path,
            [
                {"name": "architect", "agentType": "pact-architect"},
                {"name": "backend-coder", "agentType": "pact-backend-coder"},
            ],
        )

        result = get_peer_context(
            agent_type="pact-architect",
            team_name="pact-test",
            agent_name="architect",
            teams_dir=str(teams_dir),
        )

        assert result is not None
        assert "PACT plugin: unknown (root: <unset>)" in result

    def test_banner_does_not_precede_pact_role_marker(
        self, tmp_path, monkeypatch
    ):
        """Security invariant: the PACT ROLE marker at byte-0 of the
        peer context must remain the first line. Banner must land
        AFTER the prelude, per architecture §3.3 `Place banner
        BETWEEN peer_context and teachback reminder (not before
        prelude — prelude's PACT ROLE marker must remain the first
        line for the byte-0 line-anchored substring check).`"""
        from peer_inject import get_peer_context

        plugin_root = tmp_path / "installed-cache"
        claude_plugin = plugin_root / ".claude-plugin"
        claude_plugin.mkdir(parents=True)
        (claude_plugin / "plugin.json").write_text(
            json.dumps({"name": "PACT", "version": "3.18.1"})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))

        teams_dir = self._write_team_config(
            tmp_path,
            [
                {"name": "architect", "agentType": "pact-architect"},
                {"name": "backend-coder", "agentType": "pact-backend-coder"},
            ],
        )

        result = get_peer_context(
            agent_type="pact-architect",
            team_name="pact-test",
            agent_name="architect",
            teams_dir=str(teams_dir),
        )

        assert result is not None
        # The PACT ROLE marker must still be the very first bytes.
        assert result.startswith("YOUR PACT ROLE: teammate (architect)")
        banner = f"PACT plugin: PACT 3.18.1 (root: {plugin_root})"
        assert result.index(banner) > result.index("YOUR PACT ROLE:")


class TestCounterTestBySlotARevert:
    """Counter-test-by-revert for session_init Slot A append (d4f0f794
    dual-direction discipline). These tests are written so that if a
    future edit removes the `context_parts.append(format_plugin_banner())`
    call at Slot A (line 709 in session_init.py), at least one named
    test here fails with a specific, informative assertion message.

    Verified empirically during authoring: commenting out the Slot A
    append and rerunning this class produces a failure. See the
    docstring of test_banner_absent_without_slot_a_append for the
    load-bearing assertion."""

    def test_banner_present_in_additional_context(
        self, monkeypatch, tmp_path
    ):
        """Load-bearing regression guard: if Slot A is reverted, the banner
        disappears from additionalContext and this assertion fails."""
        import io as _io
        from unittest.mock import patch as _patch

        from session_init import main

        plugin_root = tmp_path / "installed-cache"
        claude_plugin = plugin_root / ".claude-plugin"
        claude_plugin.mkdir(parents=True)
        (claude_plugin / "plugin.json").write_text(
            json.dumps({"name": "PACT", "version": "3.18.1"})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "project"))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        (tmp_path / "home").mkdir(exist_ok=True)

        stdin_data = json.dumps(
            {"session_id": "abc12345-0000-0000-0000-000000000000"}
        )

        with _patch("session_init.setup_plugin_symlinks", return_value=None), \
             _patch("session_init.remove_stale_kernel_block", return_value=None), \
             _patch("session_init.update_pact_routing", return_value=None), \
             _patch("session_init.ensure_project_memory_md", return_value=None), \
             _patch("session_init.check_pinned_staleness", return_value=None), \
             _patch("session_init.update_session_info", return_value=None), \
             _patch("session_init.get_task_list", return_value=None), \
             _patch("session_init.restore_last_session", return_value=None), \
             _patch("session_init.check_paused_state", return_value=None), \
             _patch("sys.stdin", _io.StringIO(stdin_data)), \
             _patch("sys.stdout", new_callable=_io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit):
                main()

        output = json.loads(mock_stdout.getvalue())
        additional = output["hookSpecificOutput"]["additionalContext"]
        assert "PACT plugin: PACT 3.18.1" in additional, (
            "Slot A banner missing from additionalContext — verify "
            "session_init.py line 709 still appends format_plugin_banner()"
        )

    def test_format_plugin_banner_is_imported_in_session_init(self):
        """Static guard: if the import line is deleted, an import-time
        error is raised at session_init.py load, not just a quiet drop."""
        import session_init

        assert hasattr(session_init, "format_plugin_banner"), (
            "session_init must import format_plugin_banner at module scope"
        )


class TestCounterTestByPeerInjectRevert:
    """Counter-test-by-revert for peer_inject banner insertion (dual
    direction — pair with TestCounterTestBySlotARevert per d4f0f794).
    If a future edit removes the `format_plugin_banner()` call from
    the return tuple in get_peer_context() (peer_inject.py line 167),
    at least one named test here fails with a specific message.

    Verified empirically during authoring: removing the banner term
    from the return concatenation makes these tests fail."""

    def test_peer_inject_output_contains_banner(self, tmp_path, monkeypatch):
        """Load-bearing regression guard: banner must appear in
        get_peer_context() output."""
        from peer_inject import get_peer_context

        plugin_root = tmp_path / "installed-cache"
        claude_plugin = plugin_root / ".claude-plugin"
        claude_plugin.mkdir(parents=True)
        (claude_plugin / "plugin.json").write_text(
            json.dumps({"name": "PACT", "version": "3.18.1"})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))

        team_dir = tmp_path / "teams" / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps(
                {
                    "members": [
                        {"name": "architect", "agentType": "pact-architect"},
                        {
                            "name": "backend-coder",
                            "agentType": "pact-backend-coder",
                        },
                    ]
                }
            )
        )

        result = get_peer_context(
            agent_type="pact-architect",
            team_name="pact-test",
            agent_name="architect",
            teams_dir=str(tmp_path / "teams"),
        )

        assert result is not None
        assert "PACT plugin: PACT 3.18.1" in result, (
            "banner missing from peer_inject.get_peer_context() return — "
            "verify peer_inject.py line 167 still includes "
            "format_plugin_banner() in the return concatenation"
        )

    def test_format_plugin_banner_is_imported_in_peer_inject(self):
        """Static guard: import must be present at module scope."""
        import peer_inject

        assert hasattr(peer_inject, "format_plugin_banner"), (
            "peer_inject must import format_plugin_banner at module scope"
        )


class TestDogfoodPathWorktreeVsInstalledCache:
    """The #500 dogfood motivation: teammate edits worktree source at
    /Users/.../worktrees/feat-X/pact-plugin/hooks/, but the runtime
    resolves hooks against ${CLAUDE_PLUGIN_ROOT}, which is the installed
    cache at ~/.claude/plugins/cache/pact-marketplace/PACT/3.x.y. The
    banner must surface the INSTALLED-CACHE version/root — not whatever
    is in the worktree source tree.

    These tests simulate the #497 symptom:
      - "installed cache" plugin.json reports version 3.17.0 (old).
      - "worktree source" plugin.json reports version 3.99.0 (new, not
        yet released — represents the teammate's unreleased edits).
      - CLAUDE_PLUGIN_ROOT points at the installed cache.
      - The banner MUST surface 3.17.0 + the installed-cache root,
        NOT 3.99.0 and NOT the worktree path.

    Rationale: the whole purpose of the banner is to reveal this
    divergence at a glance. A banner that accidentally reflects worktree
    state would defeat the diagnostic."""

    def _setup_two_trees(self, tmp_path):
        installed_cache = tmp_path / "installed-cache"
        worktree_source = tmp_path / "worktree-source"

        for tree, version, name in [
            (installed_cache, "3.17.0", "PACT"),
            (worktree_source, "3.99.0", "PACT"),
        ]:
            cp_dir = tree / ".claude-plugin"
            cp_dir.mkdir(parents=True)
            (cp_dir / "plugin.json").write_text(
                json.dumps({"name": name, "version": version})
            )
        return installed_cache, worktree_source

    def test_banner_surfaces_installed_cache_root(self, tmp_path, monkeypatch):
        """CLAUDE_PLUGIN_ROOT=installed_cache → banner shows installed
        version, even though worktree on disk has newer version."""
        from shared.plugin_manifest import format_plugin_banner

        installed_cache, _worktree = self._setup_two_trees(tmp_path)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(installed_cache))

        banner = format_plugin_banner()

        assert banner == (
            f"PACT plugin: PACT 3.17.0 (root: {installed_cache})"
        )

    def test_banner_does_not_surface_worktree_version(
        self, tmp_path, monkeypatch
    ):
        """Negative form of the above: the worktree's 3.99.0 must NOT
        appear anywhere in the banner."""
        from shared.plugin_manifest import format_plugin_banner

        installed_cache, worktree = self._setup_two_trees(tmp_path)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(installed_cache))

        banner = format_plugin_banner()

        assert "3.99.0" not in banner
        assert str(worktree) not in banner

    def test_banner_divergence_visible_via_root_path(
        self, tmp_path, monkeypatch
    ):
        """A reader comparing banner.root vs their known worktree path
        can detect the #497 symptom. Simulated here by asserting that
        the banner's root path differs from a separate `worktree` path
        under realistic conditions — this is the diagnostic property."""
        from shared.plugin_manifest import format_plugin_banner

        installed_cache, worktree = self._setup_two_trees(tmp_path)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(installed_cache))

        banner = format_plugin_banner()

        assert str(installed_cache) in banner
        assert str(installed_cache) != str(worktree)
        # A hypothetical divergence-detection predicate a human/agent
        # reader might apply:
        reader_expected_worktree = str(worktree)
        banner_root_visible = str(installed_cache)
        assert reader_expected_worktree != banner_root_visible

    def test_banner_still_fails_open_when_installed_cache_corrupted(
        self, tmp_path, monkeypatch
    ):
        """Dogfood integrity: even when the installed cache's plugin.json
        is corrupted, the banner must still surface the installed-cache
        ROOT path (so the reader can see where the runtime is pointed)
        rather than silently falling back to <unset> or picking up the
        worktree tree."""
        from shared.plugin_manifest import format_plugin_banner

        installed_cache = tmp_path / "installed-cache"
        cp_dir = installed_cache / ".claude-plugin"
        cp_dir.mkdir(parents=True)
        (cp_dir / "plugin.json").write_text("{corrupted json")

        worktree = tmp_path / "worktree-source"
        cp_dir_w = worktree / ".claude-plugin"
        cp_dir_w.mkdir(parents=True)
        (cp_dir_w / "plugin.json").write_text(
            json.dumps({"name": "PACT", "version": "3.99.0"})
        )

        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(installed_cache))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {installed_cache})"
        assert str(worktree) not in banner
        assert "3.99.0" not in banner
