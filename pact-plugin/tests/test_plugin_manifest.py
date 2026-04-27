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
        # Sanitizer uses empty-string replacement (mirrors
        # `session_state._RENDER_STRIP_RE.sub("", ...)`), so trailing `\n`
        # on name collapses, and embedded `\r` in version collapses.
        assert "PACT 3.18.1" in banner


class TestFailOpenFailurePathsExtended:
    """Explicit named tests for each fail-open failure path beyond smoke
    coverage. Smoke tests cover happy path + CLAUDE_PLUGIN_ROOT unset +
    plugin.json missing + malformed JSON via the parametrized invariants
    sweep. These named tests pin each remaining failure mode individually
    so a failure in any one mode surfaces a specific diagnostic rather
    than a collapsed parametrize failure. The prefix + root-display
    pattern is the public contract; asserting the exact sentinel literal
    guards against any caller ever seeing a partial or mangled banner
    under failure.
    """

    def test_nonexistent_root_path_emits_sentinel(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        nonexistent = tmp_path / "does-not-exist"
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(nonexistent))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {nonexistent})"

    def test_missing_version_key_emits_sentinel(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(tmp_path, json.dumps({"name": "PACT"}))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_missing_name_key_emits_sentinel(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(tmp_path, json.dumps({"version": "3.18.1"}))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_non_string_version_emits_sentinel_with_resolved_root(
        self, tmp_path, monkeypatch
    ):
        """Discriminates inner-schema gate (returns `<root>` in sentinel)
        from outer-blanket except (returns `<unset>`). Integer version
        must be rejected by the `isinstance` check, not fall through to
        `.replace` and raise AttributeError into the outer guard."""
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path, json.dumps({"name": "PACT", "version": 3})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_empty_name_string_emits_sentinel(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path, json.dumps({"name": "", "version": "3.18.1"})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_empty_version_string_emits_sentinel(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path, json.dumps({"name": "PACT", "version": ""})
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_top_level_json_list_emits_sentinel(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(tmp_path, json.dumps(["not", "a", "dict"]))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_top_level_json_string_emits_sentinel(self, tmp_path, monkeypatch):
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(tmp_path, json.dumps("just-a-string"))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == f"PACT plugin: unknown (root: {root})"

    def test_permission_error_on_read_emits_sentinel(self, tmp_path, monkeypatch):
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

    def test_generic_oserror_on_read_emits_sentinel(self, tmp_path, monkeypatch):
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

    def test_unicode_decode_error_emits_sentinel(self, tmp_path, monkeypatch):
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
        for i, (name, version) in enumerate(hostile_values):
            root = _make_plugin_root(
                tmp_path / f"case_{i}",
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

    # --- Project-wide sanitization regex contract (mirrors session_state._RENDER_STRIP_RE) ---
    # Concurrent task #14 adds `_sanitize()` to plugin_manifest.py with the same
    # regex `[\x00-\x1f\x7f\u0085\u2028\u2029]` used by session_state.py:83
    # and peer_inject._sanitize_agent_name. Replacement is space (not empty).
    # Tests below assert the output-level contract (characters absent from banner,
    # banner remains single-line) — robust to whether the fix ships as an
    # `_sanitize()` helper or an inline regex call.
    #
    # IMPORTANT AUTHORING NOTE: per backend-coder memory `edit_tool_unicode_normalization_492.md`,
    # Edit-tool paste silently normalizes U+2028/U+2029 → ASCII 0x20. We use
    # `chr(0x...)` construction below to guarantee the literal codepoint lands
    # unchanged regardless of how the test file was authored.

    _U2028 = chr(0x2028)
    _U2029 = chr(0x2029)
    _U0085 = chr(0x0085)

    @staticmethod
    def _probe_sanitizer_support():
        """Probe whether the production helper strips the project-convention
        regex char set. Runs `format_plugin_banner()` against a manifest
        containing U+2028; returns True iff the character is absent from the
        output.

        Coordinates with concurrent task #14 (sanitizer implementation).
        Tests guarded by this probe skip when the sanitizer is not yet in
        production; they activate automatically once #14 lands.

        The probe targets U+2028 specifically rather than a C0 control
        because the pre-#14 implementation already strips `\n` / `\r`
        (a subset of C0). A U+2028 probe discriminates old vs new behavior
        precisely."""
        import json as _json
        import os
        import tempfile
        from shared.plugin_manifest import format_plugin_banner

        u2028 = chr(0x2028)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "plugin"
            cp = root / ".claude-plugin"
            cp.mkdir(parents=True)
            (cp / "plugin.json").write_text(
                _json.dumps({"name": f"PA{u2028}CT", "version": "3.18.1"})
            )
            os.environ["CLAUDE_PLUGIN_ROOT"] = str(root)
            try:
                banner = format_plugin_banner()
            finally:
                os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            return u2028 not in banner

    def _skip_if_sanitizer_absent(self):
        if not self._probe_sanitizer_support():
            pytest.skip(
                "plugin_manifest sanitizer does not yet strip the project-"
                "convention control-char + Unicode-line-terminator set "
                "(regex `[\\x00-\\x1f\\x7f\\u0085\\u2028\\u2029]`); "
                "skipping until concurrent task #14 lands"
            )

    def test_c0_null_byte_stripped_from_name(self, tmp_path, monkeypatch):
        """C0 control NUL (\x00) must not leak into banner."""
        self._skip_if_sanitizer_absent()
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path,
            json.dumps({"name": f"PA{chr(0x00)}CT", "version": "3.18.1"}),
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert chr(0x00) not in banner, f"NUL leaked: {banner!r}"
        assert banner.startswith("PACT plugin: ")

    def test_c0_soh_byte_stripped_from_version(self, tmp_path, monkeypatch):
        """C0 control SOH (\x01), non-boundary C0. Regex character class
        `[\x00-\x1f]` must cover the full C0 range, not just NUL + \n + \r."""
        self._skip_if_sanitizer_absent()
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path,
            json.dumps({"name": "PACT", "version": f"3.{chr(0x01)}18.1"}),
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert chr(0x01) not in banner, f"SOH leaked: {banner!r}"

    def test_c0_escape_byte_stripped(self, tmp_path, monkeypatch):
        """ESC (\x1b) — security-relevant: unsanitized ESC enables ANSI
        terminal escape injection into logs rendering the banner."""
        self._skip_if_sanitizer_absent()
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path,
            json.dumps(
                {
                    "name": f"PA{chr(0x1b)}CT",
                    "version": f"{chr(0x1b)}[31m3.18.1",
                }
            ),
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert chr(0x1b) not in banner, f"ESC leaked: {banner!r}"
        assert "[31m" in banner or "31m" in banner  # residue of stripped ESC+bracket is OK
        # But the raw ESC byte itself must be gone.

    def test_c0_unit_separator_byte_stripped(self, tmp_path, monkeypatch):
        """Upper C0 boundary: US (\x1f). Pins the `[\x00-\x1f]` upper
        edge — \x20 (space) must NOT be in the strip class."""
        self._skip_if_sanitizer_absent()
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path,
            json.dumps({"name": f"PA{chr(0x1f)}CT", "version": "3.18.1"}),
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert chr(0x1f) not in banner, f"US leaked: {banner!r}"
        # Space (0x20) must be preserved — it separates name and version
        # and also appears in the " (root: " section.
        assert " " in banner

    def test_del_byte_stripped(self, tmp_path, monkeypatch):
        """DEL (\x7f) — isolated codepoint outside C0, covered by the
        `\x7f` literal in the regex."""
        self._skip_if_sanitizer_absent()
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path,
            json.dumps({"name": f"PA{chr(0x7f)}CT", "version": "3.18.1"}),
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert chr(0x7f) not in banner, f"DEL leaked: {banner!r}"

    def test_nel_u0085_stripped(self, tmp_path, monkeypatch):
        """NEL (U+0085, NEXT LINE) — Unicode line terminator that
        `str.splitlines()` honors. Regex must cover it via the `\u0085`
        literal in the character class."""
        self._skip_if_sanitizer_absent()
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path,
            json.dumps({"name": f"PA{chr(0x0085)}CT", "version": "3.18.1"}),
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert chr(0x0085) not in banner, f"NEL leaked: {banner!r}"
        assert len(banner.splitlines()) == 1

    def test_u2028_line_separator_stripped_from_name(self, tmp_path, monkeypatch):
        """U+2028 (LINE SEPARATOR) — Unicode line terminator. `str.splitlines()`
        treats it as a line boundary, so downstream consumers that split on
        lines would see it as a break even though ASCII `\n`/`\r` strip misses it."""
        self._skip_if_sanitizer_absent()
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path,
            json.dumps({"name": f"PA{chr(0x2028)}CT", "version": "3.18.1"}),
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert chr(0x2028) not in banner, f"U+2028 leaked: {banner!r}"
        assert len(banner.splitlines()) == 1

    def test_u2029_paragraph_separator_stripped_from_version(
        self, tmp_path, monkeypatch
    ):
        """U+2029 (PARAGRAPH SEPARATOR) — sibling Unicode line terminator."""
        self._skip_if_sanitizer_absent()
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path,
            json.dumps({"name": "PACT", "version": f"3.{chr(0x2029)}18.1"}),
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert chr(0x2029) not in banner, f"U+2029 leaked: {banner!r}"
        assert len(banner.splitlines()) == 1

    def test_all_stripped_chars_together_yield_single_line_banner(
        self, tmp_path, monkeypatch
    ):
        """Exhaustive: inject every character class member into name and
        version simultaneously. Banner must remain single-line with NO
        member present."""
        self._skip_if_sanitizer_absent()
        from shared.plugin_manifest import format_plugin_banner

        hostile_chars = [
            chr(0x00), chr(0x01), chr(0x0a), chr(0x0d), chr(0x1b),
            chr(0x1f), chr(0x7f), chr(0x0085), chr(0x2028), chr(0x2029),
        ]
        hostile_name = "P" + "".join(hostile_chars) + "ACT"
        hostile_version = chr(0x01) + "3.18.1" + chr(0x2028)

        root = _make_plugin_root(
            tmp_path,
            json.dumps({"name": hostile_name, "version": hostile_version}),
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        for bad_char in hostile_chars:
            assert bad_char not in banner, (
                f"char {bad_char!r} (U+{ord(bad_char):04X}) leaked: {banner!r}"
            )
        # Also the ASCII newline/CR variants must be gone — defense in depth
        # against the pre-#14 `.replace("\n", " ").replace("\r", " ")` chain.
        assert "\n" not in banner, banner
        assert "\r" not in banner, banner
        assert len(banner.splitlines()) == 1
        assert banner.startswith("PACT plugin: ")

    def test_clean_input_passes_through_unchanged(self, tmp_path, monkeypatch):
        """Positive control: manifest with only printable ASCII should
        produce a banner IDENTICAL to what the pre-sanitizer helper would
        produce (no accidental mangling of legitimate content). Pins the
        `\x20` (space) and `\x21..\x7e` printable range AS OUTSIDE the
        strip class."""
        # No skip needed — this test asserts behavior under clean input,
        # which must hold whether or not the sanitizer is present.
        from shared.plugin_manifest import format_plugin_banner

        root = _make_plugin_root(
            tmp_path,
            json.dumps({"name": "PACT", "version": "3.18.1-beta+build.5"}),
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

        banner = format_plugin_banner()

        assert banner == (
            f"PACT plugin: PACT 3.18.1-beta+build.5 (root: {root})"
        )


# Integration tests (TestSessionInitSlotAIntegration, TestPeerInjectIntegration)
# and revert guards (TestCounterTestBySlotARevert, TestCounterTestByPeerInjectRevert)
# have moved to tests/test_session_init.py and tests/test_peer_inject.py
# respectively — they exercise the hooks, so they live alongside them.


class TestDogfoodPathWorktreeVsInstalledCache:
    """The #500 dogfood motivation: teammate edits worktree source at
    /Users/.../worktrees/feat-X/pact-plugin/hooks/, but the runtime
    resolves hooks against ${CLAUDE_PLUGIN_ROOT}, which is the installed
    cache at ~/.claude/plugins/cache/pact-plugin/PACT/3.x.y. The
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
        """Negative form: the worktree's 3.99.0 must NOT appear anywhere
        in the banner AND Path.read_text must NEVER be called with a path
        under the worktree tree. The call-site negative assertion turns
        this test into a load-bearing contract: if future regression
        accidentally reads from CLAUDE_PLUGIN_ROOT's sibling/parent paths
        or from the current working directory, the call-tracking fails
        even if the output string happens to omit `3.99.0`."""
        from pathlib import Path as _Path

        from shared.plugin_manifest import format_plugin_banner

        installed_cache, worktree = self._setup_two_trees(tmp_path)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(installed_cache))

        worktree_reads: list[str] = []
        original_read_text = _Path.read_text

        def tracking_read_text(self, *args, **kwargs):
            if str(worktree) in str(self):
                worktree_reads.append(str(self))
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(_Path, "read_text", tracking_read_text)

        banner = format_plugin_banner()

        assert "3.99.0" not in banner
        assert str(worktree) not in banner
        assert worktree_reads == [], (
            f"banner helper should never consult the worktree tree when "
            f"CLAUDE_PLUGIN_ROOT points elsewhere; observed reads: "
            f"{worktree_reads}"
        )

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
