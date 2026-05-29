"""Tests for shared/teammate_mode.py -- effective teammateMode resolution and
the in-process startup-notice emit decision.

Background: the helper mirrors the FILE-READABLE portion of Claude Code's
settings precedence to answer "what is the effective teammateMode?" from a hook
subprocess (which cannot see the live `--teammate-mode` CLI override). It is
fail-open by construction -- every public function is total (never raises),
because the sole consumer runs on the SessionStart hot path where an uncaught
exception would break bootstrap.

Core invariant under test: NEVER FALSE-SUPPRESS. A false suppress silently
reinstates the in-process idle-stall the notice exists to warn about; a false
emit is one harmless extra startup line. Every fail-open / fail-safe decision
resolves toward EMIT.

(Accepted breaches, NOT tested here because they are unreadable from a hook
subprocess: the in-memory `--teammate-mode` CLI override and the enterprise
managed-settings layer. Both are documented Phase-1 blind spots that only ever
err toward over-emitting -- the safe direction.)

Coverage:

resolve_effective_teammate_mode() + should_emit_inprocess_notice() -- §9.1 matrix:
1.  user settings.json {in-process}                 -> "in-process" / emit
2.  user settings.json {tmux}                        -> "tmux"       / suppress
3.  user settings.json {auto}                         -> "auto"       / emit
4.  no settings files anywhere                        -> "auto"       / emit (default)
5.  user settings.json malformed JSON                 -> "auto"       / emit (skip)
6.  user settings.json {tmux} + ~/.claude.json {in-process}
                                                       -> "tmux"       / suppress (settings>legacy)
7.  only ~/.claude.json {in-process} (no settings)    -> "in-process" / emit (legacy fallback)
8.  project settings.local.json {in-process} OVER user settings.json {tmux}
                                                       -> "in-process" / emit (FALSE-SUPPRESS EDGE)
9.  {teammateMode:"banana"} (unrecognized)            -> "auto"       / emit (value skipped)
10. {teammateMode: 42} (non-string)                   -> "auto"       / emit (type guard skips)

Precedence layering (defense-in-depth beyond the architect's 10):
- project settings.json beats user settings.json
- project settings.local.json beats project settings.json
- empty-string teammateMode is skipped like any unrecognized value

VALID_TEAMMATE_MODES contract:
- exact membership + frozenset immutability

Fail-open guarantee (§9.3) -- helper never raises under any read/parse error:
- malformed JSON / non-dict JSON / unreadable (dir-in-place) / wrong-type value
- Path.home() itself raising (outer-guard defense in depth)
"""
import json
import sys
from pathlib import Path

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import shared.teammate_mode as teammate_mode
from shared.teammate_mode import (
    VALID_TEAMMATE_MODES,
    _read_teammate_mode,
    _settings_source_paths,
    resolve_effective_teammate_mode,
    should_emit_inprocess_notice,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _ModeEnv:
    """Filesystem-isolated settings-source writer for teammate_mode tests.

    Pins Path.home() -> tmp_path/home and CLAUDE_PROJECT_DIR -> tmp_path/project
    so the helper reads ONLY the temp tree, never the real machine's settings
    (devops flagged this determinism risk). Exposes one writer per settings
    source so tests construct exactly the precedence they intend.
    """

    def __init__(self, tmp_path, monkeypatch):
        self.home = tmp_path / "home"
        self.project = tmp_path / "project"
        self.home.mkdir()
        self.project.mkdir()
        monkeypatch.setattr(Path, "home", lambda: self.home)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(self.project))

    @staticmethod
    def _write(path: Path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        # dict -> JSON; str written raw (for malformed-JSON / non-dict cases)
        text = content if isinstance(content, str) else json.dumps(content)
        path.write_text(text, encoding="utf-8")
        return path

    # Settings sources, highest precedence first.
    def write_project_local(self, content):
        return self._write(self.project / ".claude" / "settings.local.json", content)

    def write_project(self, content):
        return self._write(self.project / ".claude" / "settings.json", content)

    def write_user(self, content):
        return self._write(self.home / ".claude" / "settings.json", content)

    def write_legacy(self, content):
        return self._write(self.home / ".claude.json", content)


@pytest.fixture
def mode_env(tmp_path, monkeypatch):
    """Isolated settings tree for resolve/should_emit tests."""
    return _ModeEnv(tmp_path, monkeypatch)


# ---------------------------------------------------------------------------
# §9.1 matrix: resolve_effective_teammate_mode + should_emit_inprocess_notice
# ---------------------------------------------------------------------------

class TestResolveAndEmitMatrix:
    """The architect's §9.1 matrix: resolution value AND the emit decision.

    should_emit is asserted alongside resolve in every row because the emit
    decision is the load-bearing output (#864). Testing them together pins the
    'never false-suppress' invariant at the boundary the safeguard defends.
    """

    def test_user_in_process_emits(self, mode_env):
        """Row 1: user settings in-process -> resolve in-process, EMIT."""
        mode_env.write_user({"teammateMode": "in-process"})
        assert resolve_effective_teammate_mode() == "in-process"
        assert should_emit_inprocess_notice() is True

    def test_user_tmux_suppresses(self, mode_env):
        """Row 2: user settings tmux -> resolve tmux, SUPPRESS (the only suppress case)."""
        mode_env.write_user({"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "tmux"
        assert should_emit_inprocess_notice() is False

    def test_user_auto_emits(self, mode_env):
        """Row 3: user settings auto -> resolve auto, EMIT (may resolve to in-process at runtime)."""
        mode_env.write_user({"teammateMode": "auto"})
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True

    def test_no_settings_anywhere_defaults_auto_emits(self, mode_env):
        """Row 4: nothing defines the key -> default auto, EMIT (fail-safe default)."""
        # mode_env writes nothing.
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True

    def test_user_malformed_json_skips_to_default_emits(self, mode_env):
        """Row 5: malformed JSON -> source skipped -> default auto, EMIT (fail-open)."""
        mode_env.write_user("{ this is not valid json ")
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True

    def test_settings_wins_over_legacy(self, mode_env):
        """Row 6: settings tmux + legacy in-process -> tmux, SUPPRESS (live-machine case).

        Settings sources outrank the ~/.claude.json legacy fallback. This is
        the common live-machine shape and must SUPPRESS -- the legacy
        in-process value must NOT leak through and force an emit.
        """
        mode_env.write_user({"teammateMode": "tmux"})
        mode_env.write_legacy({"teammateMode": "in-process"})
        assert resolve_effective_teammate_mode() == "tmux"
        assert should_emit_inprocess_notice() is False

    def test_legacy_only_fallback_used(self, mode_env):
        """Row 7: only ~/.claude.json in-process (no settings file) -> in-process, EMIT.

        The legacy fallback is consulted when no settings source defines the key.
        """
        mode_env.write_legacy({"teammateMode": "in-process"})
        assert resolve_effective_teammate_mode() == "in-process"
        assert should_emit_inprocess_notice() is True

    def test_false_suppress_edge_project_local_over_user_tmux(self, mode_env):
        """Row 8: THE FALSE-SUPPRESS EDGE -- project-local in-process OVER user tmux.

        This is the exact #864-reinstating direction FULL precedence exists to
        prevent. A two-file shortcut reader would see the user 'tmux' and
        SUPPRESS, while the runtime actually resolves the project-local
        'in-process' and the notice SHOULD fire. FULL precedence reads
        project-local first -> 'in-process' -> EMIT.

        If this test ever inverts (resolves tmux / suppresses), the precedence
        assumption the whole design rests on has broken.
        """
        mode_env.write_project_local({"teammateMode": "in-process"})
        mode_env.write_user({"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "in-process"
        assert should_emit_inprocess_notice() is True

    def test_unrecognized_value_skipped_emits(self, mode_env):
        """Row 9: {teammateMode:'banana'} -> value skipped -> default auto, EMIT."""
        mode_env.write_user({"teammateMode": "banana"})
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True

    def test_non_string_value_skipped_emits(self, mode_env):
        """Row 10: {teammateMode: 42} -> type guard skips -> default auto, EMIT."""
        mode_env.write_user({"teammateMode": 42})
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True


# ---------------------------------------------------------------------------
# Precedence layering -- defense-in-depth beyond the architect's 10 rows
# ---------------------------------------------------------------------------

class TestPrecedenceLayering:
    """Each higher-priority source must shadow the lower one.

    Row 8 pins layer-1 (project-local) over layer-3 (user). These pin the
    intermediate edges so a future re-ordering of any single layer fails loudly.
    """

    def test_project_settings_beats_user_settings(self, mode_env):
        """project settings.json (layer 2) outranks user settings.json (layer 3)."""
        mode_env.write_project({"teammateMode": "in-process"})
        mode_env.write_user({"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "in-process"
        assert should_emit_inprocess_notice() is True

    def test_project_local_beats_project_settings(self, mode_env):
        """project settings.local.json (layer 1) outranks project settings.json (layer 2)."""
        mode_env.write_project_local({"teammateMode": "tmux"})
        mode_env.write_project({"teammateMode": "in-process"})
        assert resolve_effective_teammate_mode() == "tmux"
        assert should_emit_inprocess_notice() is False

    def test_full_chain_local_wins_over_all_lower(self, mode_env):
        """All four sources set; the highest (project-local) wins outright."""
        mode_env.write_project_local({"teammateMode": "in-process"})
        mode_env.write_project({"teammateMode": "tmux"})
        mode_env.write_user({"teammateMode": "tmux"})
        mode_env.write_legacy({"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "in-process"
        assert should_emit_inprocess_notice() is True

    def test_higher_source_without_key_falls_through(self, mode_env):
        """A higher source that omits the key does NOT shadow a lower one.

        project settings.local.json has no teammateMode (defines other keys);
        resolution must fall through to the user 'tmux'.
        """
        mode_env.write_project_local({"permissions": {"additionalDirectories": []}})
        mode_env.write_user({"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "tmux"
        assert should_emit_inprocess_notice() is False

    def test_empty_string_value_skipped(self, mode_env):
        """teammateMode:'' is not in VALID_TEAMMATE_MODES -> skipped like 'banana'."""
        mode_env.write_user({"teammateMode": ""})
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True


# ---------------------------------------------------------------------------
# VALID_TEAMMATE_MODES contract
# ---------------------------------------------------------------------------

class TestValidModesContract:
    """Pin the allowed value set and its immutability.

    If Claude Code adds/renames a teammateMode value, this test is the canary:
    the helper resolves unknown values to 'auto' (safe / emit), so a schema
    drift degrades gracefully, but the pinned set documents the known contract.
    """

    def test_valid_modes_exact_membership(self):
        assert VALID_TEAMMATE_MODES == frozenset({"auto", "tmux", "in-process"})

    def test_valid_modes_is_frozenset(self):
        """Immutable so it cannot be mutated by an importer at runtime."""
        assert isinstance(VALID_TEAMMATE_MODES, frozenset)
        with pytest.raises(AttributeError):
            VALID_TEAMMATE_MODES.add("new-mode")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# §9.3 fail-open guarantee -- the helper is TOTAL (never raises)
# ---------------------------------------------------------------------------

class TestReadTeammateModeFailOpen:
    """Per-file reader contract (§4.1 _read_teammate_mode): any missing /
    unreadable / parse-error / wrong-type / unrecognized condition returns
    None and NEVER raises.
    """

    def test_missing_file_returns_none(self, tmp_path):
        assert _read_teammate_mode(tmp_path / "nope.json") is None

    def test_malformed_json_returns_none(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text("{ not json", encoding="utf-8")
        assert _read_teammate_mode(p) is None

    def test_non_dict_json_returns_none(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert _read_teammate_mode(p) is None

    def test_unreadable_path_is_a_directory_returns_none(self, tmp_path):
        """exists() is True but read_text raises (IsADirectoryError) -> None, no raise."""
        p = tmp_path / "settings.json"
        p.mkdir()
        assert _read_teammate_mode(p) is None

    def test_wrong_type_value_returns_none(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"teammateMode": ["tmux"]}), encoding="utf-8")
        assert _read_teammate_mode(p) is None

    def test_unrecognized_value_returns_none(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"teammateMode": "banana"}), encoding="utf-8")
        assert _read_teammate_mode(p) is None

    def test_recognized_value_returned(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"teammateMode": "tmux"}), encoding="utf-8")
        assert _read_teammate_mode(p) == "tmux"


class TestResolveFailOpen:
    """resolve_effective_teammate_mode + should_emit are TOTAL: any failure
    degrades to 'auto' / EMIT, never an exception out of the hot path.
    """

    def test_unreadable_source_degrades_to_auto_emit(self, mode_env):
        """A settings.json that is a directory (read raises) -> auto / EMIT."""
        (mode_env.home / ".claude").mkdir(parents=True)
        (mode_env.home / ".claude" / "settings.json").mkdir()
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True

    def test_path_home_raising_degrades_to_auto(self, mode_env, monkeypatch):
        """If Path.home() itself raises, the outer guard returns the default.

        _settings_source_paths() calls Path.home(); if that throws, the whole
        source-list construction raises and resolve's outer try/except returns
        _DEFAULT_MODE. Defense-in-depth beyond per-file fail-open.
        """
        def boom():
            raise RuntimeError("home unavailable")

        monkeypatch.setattr(Path, "home", boom)
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True

    def test_should_emit_isolated_from_resolve_failure(self, monkeypatch):
        """should_emit's own try/except returns True (EMIT) if resolve raises.

        Belt-and-suspenders: even if resolve_effective_teammate_mode were to
        raise (it is total, but defense-in-depth matters on the hot path),
        should_emit must fail-safe toward EMIT, never propagate.
        """
        def boom():
            raise RuntimeError("resolve blew up")

        monkeypatch.setattr(teammate_mode, "resolve_effective_teammate_mode", boom)
        assert should_emit_inprocess_notice() is True


# ---------------------------------------------------------------------------
# _settings_source_paths -- precedence-order contract
# ---------------------------------------------------------------------------

class TestSettingsSourcePaths:
    """Pin the file-readable precedence order and the CLAUDE_PROJECT_DIR seam."""

    def test_paths_in_precedence_order(self, mode_env):
        paths = _settings_source_paths()
        assert paths == [
            mode_env.project / ".claude" / "settings.local.json",
            mode_env.project / ".claude" / "settings.json",
            mode_env.home / ".claude" / "settings.json",
        ]

    def test_project_dir_defaults_to_cwd_when_unset(self, monkeypatch, tmp_path):
        """Unset CLAUDE_PROJECT_DIR -> project paths derive from '.' (never raises)."""
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        paths = _settings_source_paths()
        assert paths[0] == Path(".") / ".claude" / "settings.local.json"
        assert paths[1] == Path(".") / ".claude" / "settings.json"
        assert paths[2] == tmp_path / ".claude" / "settings.json"


# ---------------------------------------------------------------------------
# Anti-normalization regression guard (documentation-in-code)
# ---------------------------------------------------------------------------

class TestAntiNormalizationVariants:
    """Pin case-variant and whitespace-padded values to EMIT -- on purpose.

    The helper does an EXACT check: a value is recognized only if it is
    byte-identical to a member of VALID_TEAMMATE_MODES, and should_emit
    suppresses only on resolved == "tmux". So every case-variant ("Tmux",
    "TMUX") and whitespace-padded (" tmux", "tmux ") value is NOT recognized
    -> the source is skipped -> resolution falls through to "auto" -> the
    notice EMITS. That EMIT is the fail-open-SAFE direction: a tmux user who
    typed "Tmux" gets one harmless extra startup line.

    These tests exist to FAIL LOUDLY if a future "be lenient" refactor adds
    case/whitespace normalization (e.g. `value.strip().lower() in
    VALID_TEAMMATE_MODES`) to _read_teammate_mode. Such a refactor would
    silently flip these variants to the SUPPRESS direction -- a FALSE-SUPPRESS,
    the exact #864-reinstating outcome the core invariant forbids -- while
    passing every other test in this file. The test IS the guard: do NOT
    "fix" these to suppress.
    """

    @pytest.mark.parametrize("variant", ["Tmux", "TMUX", "tMux"])
    def test_case_variant_emits(self, mode_env, variant):
        """A case-variant of 'tmux' is not byte-equal -> skipped -> auto -> EMIT.

        Failure here (resolve 'tmux' / suppress) means someone added
        case-normalization that introduced a false-suppress. See class docstring.
        """
        mode_env.write_user({"teammateMode": variant})
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True

    @pytest.mark.parametrize("variant", [" tmux", "tmux ", " tmux ", "\ttmux", "tmux\n"])
    def test_whitespace_padded_emits(self, mode_env, variant):
        """A whitespace-padded 'tmux' is not byte-equal -> skipped -> auto -> EMIT.

        Failure here (resolve 'tmux' / suppress) means someone added
        whitespace-stripping that introduced a false-suppress. See class docstring.
        """
        mode_env.write_user({"teammateMode": variant})
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True


# ---------------------------------------------------------------------------
# Broad-except CONTRACT: the breadth of the per-file handler is load-bearing
# ---------------------------------------------------------------------------

class TestPerFileBroadExceptContract:
    """Pin the BREADTH of _read_teammate_mode's `except Exception` -- not just
    that it catches *something*.

    The per-file reader wraps its body in a broad `except Exception` so ANY
    read/parse failure degrades to None (fail-open). The existing fail-open
    tests only exercise exceptions a NARROW tuple would STILL catch:
    IsADirectoryError / PermissionError (OSError) and malformed JSON
    (json.JSONDecodeError). None of them would fail if someone "tightened" the
    handler to `except (OSError, json.JSONDecodeError)`.

    This closes that gap with an input whose exception is OUTSIDE that narrow
    tuple: a file whose bytes are not valid UTF-8. read_text(encoding="utf-8")
    then raises UnicodeDecodeError -- a subclass of ValueError, and NOT a
    subclass of OSError or json.JSONDecodeError (verified on CPython 3.14).
    Only the broad `except Exception` catches it; a narrowed handler would let
    it propagate and FAIL test_invalid_utf8_returns_none_via_broad_except --
    which is exactly the regression signal we want.

    NOTE: an embedded-NUL path is deliberately NOT used here. On CPython 3.12+
    Path.exists() returns False for a NUL path (it does not raise), so
    _read_teammate_mode short-circuits at its `if not path.exists()` guard
    WITHOUT entering the try/except body -- a NUL test would be phantom-green
    (it passes whether or not the except is narrowed). Invalid UTF-8 is the
    live discriminator on this interpreter.
    """

    def test_invalid_utf8_returns_none_via_broad_except(self, tmp_path):
        """Invalid-UTF-8 bytes -> UnicodeDecodeError (ValueError, not OSError /
        JSONDecodeError) -> only the broad except returns None. THE discriminator.
        """
        p = tmp_path / "settings.json"
        # Lone 0xFF/0xFE bytes inside an otherwise-JSON document: read_text
        # with strict utf-8 raises UnicodeDecodeError before json.loads runs.
        p.write_bytes(b'{"teammateMode": "\xff\xfetmux"}')
        assert _read_teammate_mode(p) is None

    def test_resolve_never_raises_on_invalid_utf8_source(self, mode_env):
        """End-to-end public never-raises contract under a non-OSError parse
        failure: an invalid-UTF-8 user settings.json -> resolve degrades to
        'auto' / EMIT, never propagating onto the SessionStart hot path.

        Defense-in-depth: resolve's OWN outer `except Exception` also backstops
        a propagating UnicodeDecodeError, so this end-to-end assertion would
        survive a per-file narrowing on its own. The per-file discriminator
        above is the test that actually fails on a narrowed per-file handler;
        this one pins the public contract that the hot path never sees a raise.
        """
        (mode_env.home / ".claude").mkdir(parents=True, exist_ok=True)
        (mode_env.home / ".claude" / "settings.json").write_bytes(
            b'{"teammateMode": "\xff\xfetmux"}'
        )
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True
