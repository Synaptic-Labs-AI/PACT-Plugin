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

(Accepted breach, NOT tested here because it is unreadable from a hook
subprocess: the in-memory `--teammate-mode` CLI override. It only ever errs
toward over-emitting -- the safe direction. The enterprise managed-settings
layer USED to be an untested blind spot too, but F1 now reads it at the TOP of
the precedence (see TestManagedSettingsPath / TestManagedPrecedence below), so
it is no longer a blind spot: a managed "in-process"/"auto" over a lower-layer
"tmux" now resolves correctly instead of false-suppressing.)

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

Enterprise managed-settings precedence (F1):
- _managed_settings_path() returns the correct OS-specific absolute literal
- managed-settings is the HIGHEST precedence source (paths[0])
- ** breach-closure regression: managed "in-process" OVER user "tmux"
  resolves "in-process" / EMIT (the false-suppress F1 closes)
- symmetric: managed "tmux" OVER user "in-process" resolves "tmux" / SUPPRESS
  (managed truly drives resolution both ways; closes the OQ1 over-emit)
- managed-absent falls through to lower layers (dev-machine common case)
- malformed managed file fails open (skip -> fall through -> never raises)

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
    _managed_settings_path,
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
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.project = tmp_path / "project"
        self.managed = tmp_path / "managed-settings.json"
        self.home.mkdir()
        self.project.mkdir()
        self._monkeypatch = monkeypatch
        monkeypatch.setattr(Path, "home", lambda: self.home)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(self.project))
        # Default-neutralize the enterprise managed-settings source so a test
        # that does NOT opt into write_managed() does not read the REAL OS
        # managed path as the highest-precedence source[0]. Without this, every
        # resolve/should_emit test silently reads
        # /Library/Application Support/ClaudeCode/managed-settings.json (or the
        # OS equivalent): green on a dev machine only because that file is
        # absent, but non-deterministic on a managed fleet -- exactly the
        # environment the managed-settings precedence targets. Mirrors the
        # Path.home() / CLAUDE_PROJECT_DIR isolation above. Tests that WANT a
        # managed file still opt in explicitly via write_managed(); the
        # managed-absent case opts in via point_managed_at_absent().
        self._default_absent_managed = tmp_path / "default-absent-managed-settings.json"
        monkeypatch.setattr(
            teammate_mode, "_managed_settings_path",
            lambda: self._default_absent_managed,
        )

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

    # Enterprise managed-settings (F1) -- highest precedence. Isolation seam:
    # monkeypatch teammate_mode._managed_settings_path so the test owns the
    # highest source WITHOUT touching a real OS managed-settings path.
    def write_managed(self, content):
        """Write a managed-settings.json under tmp and point the helper at it."""
        self._write(self.managed, content)
        self._monkeypatch.setattr(
            teammate_mode, "_managed_settings_path", lambda: self.managed
        )
        return self.managed

    def point_managed_at_absent(self):
        """Point _managed_settings_path at an absent tmp file (managed-absent case).

        Deterministic stand-in for the dev-machine common case where the real
        OS managed path does not exist -- avoids depending on the host actually
        lacking /Library/Application Support/ClaudeCode/managed-settings.json.
        """
        absent = self.root / "absent-managed-settings.json"
        self._monkeypatch.setattr(
            teammate_mode, "_managed_settings_path", lambda: absent
        )
        return absent


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
    """Pin the file-readable precedence order and the CLAUDE_PROJECT_DIR seam.

    F1: the enterprise managed-settings path is prepended as the HIGHEST
    precedence source (paths[0]); the file-readable list is now 4 entries:
    managed -> project-local -> project -> user.
    """

    def test_paths_in_precedence_order(self, mode_env):
        """4 paths, managed highest. Asserts against the SAME managed-path
        getter the resolver uses (same-function compare) so it is
        platform-agnostic -- no hardcoded OS literal in the order assertion.

        Reads teammate_mode._managed_settings_path (the MODULE attribute) rather
        than the from-imported name: the mode_env fixture default-neutralizes
        the module attribute (see _ModeEnv.__init__), and _settings_source_paths
        resolves the getter through the module namespace at call time. Comparing
        against the module attribute keeps both sides referring to the same
        (neutralized) getter; the bare from-imported name would still point at
        the original real-OS getter and spuriously mismatch.
        """
        paths = _settings_source_paths()
        assert paths == [
            teammate_mode._managed_settings_path(),
            mode_env.project / ".claude" / "settings.local.json",
            mode_env.project / ".claude" / "settings.json",
            mode_env.home / ".claude" / "settings.json",
        ]

    def test_project_dir_defaults_to_cwd_when_unset(self, monkeypatch, tmp_path):
        """Unset CLAUDE_PROJECT_DIR -> project paths derive from '.' (never raises).

        Managed stays at paths[0] (F1, highest); the cwd-default project-local
        is now paths[1], project paths[2], user paths[3] (index +1 vs pre-F1).
        """
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        paths = _settings_source_paths()
        assert paths[0] == _managed_settings_path()
        assert paths[1] == Path(".") / ".claude" / "settings.local.json"
        assert paths[2] == Path(".") / ".claude" / "settings.json"
        assert paths[3] == tmp_path / ".claude" / "settings.json"


# ---------------------------------------------------------------------------
# Enterprise managed-settings (F1): OS-specific path + highest-precedence reads
# ---------------------------------------------------------------------------

class TestManagedSettingsPath:
    """_managed_settings_path() returns the correct OS-specific absolute literal.

    Pure path construction -- no I/O, no Path.home()/expanduser -- so it does
    not perturb the home-monkeypatch seam. The literals are verified live
    against Claude Code 2.1.156 (architect OQ1 / F1).
    """

    @pytest.mark.parametrize(
        "platform,expected",
        [
            ("darwin", Path("/Library/Application Support/ClaudeCode/managed-settings.json")),
            ("win32", Path(r"C:\Program Files\ClaudeCode\managed-settings.json")),
            ("linux", Path("/etc/claude-code/managed-settings.json")),
        ],
    )
    def test_managed_path_per_os(self, monkeypatch, platform, expected):
        """Each platform maps to its documented absolute managed-settings path."""
        monkeypatch.setattr(sys, "platform", platform)
        assert _managed_settings_path() == expected

    def test_unknown_platform_defaults_to_linux_path(self, monkeypatch):
        """An unrecognized sys.platform falls to the linux/default literal."""
        monkeypatch.setattr(sys, "platform", "freebsd13")
        assert _managed_settings_path() == Path("/etc/claude-code/managed-settings.json")


class TestManagedPrecedence:
    """Managed-settings is the highest-precedence file source (F1).

    Isolation: mode_env.write_managed()/point_managed_at_absent() monkeypatch
    teammate_mode._managed_settings_path to a tmp file the test owns, so these
    NEVER read or write a real OS managed-settings path.
    """

    def test_managed_in_process_over_user_tmux_breach_closed(self, mode_env):
        """** MANAGED-SETTINGS BREACH-CLOSURE REGRESSION (F1). **

        managed-settings 'in-process' layered OVER a user 'tmux' MUST resolve
        'in-process' / EMIT. This is the exact false-suppress F1 closes: before
        F1 the helper never read managed-settings, so a managed fleet pinning
        'in-process' over a user 'tmux' was wrongly SUPPRESSED (the
        #864-reinstating direction). Managed now sits at precedence [0], read
        first -> 'in-process' -> EMIT.

        If this ever inverts (resolve 'tmux' / suppress), the managed layer has
        stopped being read at the top of the precedence and the breach is open
        again.
        """
        mode_env.write_managed({"teammateMode": "in-process"})
        mode_env.write_user({"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "in-process"
        assert should_emit_inprocess_notice() is True

    def test_managed_auto_over_user_tmux_breach_closed(self, mode_env):
        """** MANAGED 'auto' BREACH-CLOSURE (auto-flavor of the in-process case). **

        managed-settings 'auto' layered OVER a user 'tmux' MUST resolve 'auto' /
        EMIT. 'auto' is a non-tmux value, so the same false-suppress F1 closes
        applies: before F1 the helper never read managed-settings, so a managed
        fleet pinning 'auto' over a user 'tmux' was wrongly SUPPRESSED. Managed
        sits at precedence [0], read first -> 'auto' -> EMIT (fail-safe: 'auto'
        may resolve to in-process at runtime).

        Symmetric companion to test_managed_in_process_over_user_tmux_breach_closed:
        both pin that ANY non-tmux managed value over a lower 'tmux' EMITS. If
        this inverts (resolve 'tmux' / suppress), the managed layer has stopped
        being read at the top of the precedence and the breach is open again.
        """
        mode_env.write_managed({"teammateMode": "auto"})
        mode_env.write_user({"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True

    def test_managed_tmux_over_user_in_process_suppresses(self, mode_env):
        """Symmetric direction: managed 'tmux' OVER user 'in-process' -> tmux / SUPPRESS.

        Proves managed genuinely DRIVES resolution both ways (not just that an
        absent managed file falls through). Also closes architect OQ1: a
        managed-tmux fleet that previously OVER-emitted the notice now correctly
        suppresses -- because the fleet really is in tmux, this is a true
        suppress, not a false one.
        """
        mode_env.write_managed({"teammateMode": "tmux"})
        mode_env.write_user({"teammateMode": "in-process"})
        assert resolve_effective_teammate_mode() == "tmux"
        assert should_emit_inprocess_notice() is False

    def test_managed_absent_falls_through_to_lower_layers(self, mode_env):
        """managed path absent -> read lower layers normally (dev-machine case).

        Zero behavior change off managed fleets: the user 'tmux' wins exactly as
        it did pre-F1. Uses a controlled absent tmp path (not the real OS path)
        for determinism.
        """
        mode_env.point_managed_at_absent()
        mode_env.write_user({"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "tmux"
        assert should_emit_inprocess_notice() is False

    def test_managed_malformed_fails_open_to_lower_layers(self, mode_env):
        """Malformed managed file -> skipped (fail-open) -> fall through; never raises.

        A corrupt highest-precedence source must NOT crash the hot path nor
        wrongly suppress: it is skipped like any unreadable source and
        resolution continues to the lower layers (here user 'in-process' -> EMIT).
        """
        mode_env.write_managed("{ this is not valid json ")
        mode_env.write_user({"teammateMode": "in-process"})
        assert resolve_effective_teammate_mode() == "in-process"
        assert should_emit_inprocess_notice() is True

    def test_managed_unreadable_dir_fails_open(self, mode_env, monkeypatch):
        """Managed path is a directory (read raises) -> fail-open skip -> fall through.

        Defense-in-depth alongside the malformed case: a non-OSError-free read
        failure on the highest source still degrades safely to the lower layers.
        """
        managed_dir = mode_env.root / "managed-as-dir.json"
        managed_dir.mkdir()
        monkeypatch.setattr(teammate_mode, "_managed_settings_path", lambda: managed_dir)
        mode_env.write_user({"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "tmux"
        assert should_emit_inprocess_notice() is False


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


class TestConfigDirSettingsAndLegacy:
    """C6 (#926): the user settings.json source (:120) and the legacy
    ~/.claude.json fallback (:147) follow CLAUDE_CONFIG_DIR. The legacy file is
    a SIBLING-asymmetry special case: $HOME/.claude.json when unset (NOT
    $HOME/.claude/.claude.json), $CONFIG_DIR/.claude.json when set."""

    def test_user_settings_follows_config_dir(self, mode_env, monkeypatch):
        # :120 — env-set: the user settings source is $CONFIG/settings.json.
        # NON-VACUOUS: if it still read $HOME/.claude/settings.json, the file
        # below wouldn't be found and resolution would fall through to "auto".
        config_dir = mode_env.root / "config-kimi"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        mode_env._write(config_dir / "settings.json", {"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "tmux"

    def test_old_home_settings_not_read_when_config_dir_set(self, mode_env, monkeypatch):
        # env-set: the OLD $HOME/.claude/settings.json is NOT in the source list.
        config_dir = mode_env.root / "config-kimi"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        mode_env.write_user({"teammateMode": "tmux"})  # $HOME/.claude/settings.json
        # $CONFIG has no settings + no legacy → default
        assert resolve_effective_teammate_mode() == "auto"

    def test_legacy_claude_json_follows_config_dir_when_set(self, mode_env, monkeypatch):
        # :147 — env-set: legacy reads $CONFIG/.claude.json.
        config_dir = mode_env.root / "config-kimi"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        mode_env._write(config_dir / ".claude.json", {"teammateMode": "in-process"})
        assert resolve_effective_teammate_mode() == "in-process"

    def test_legacy_claude_json_preserves_home_sibling_when_unset(self, mode_env, monkeypatch):
        # :147 — env-UNSET: legacy reads $HOME/.claude.json (sibling). NON-VACUOUS:
        # a blind get_claude_config_dir()/".claude.json" would yield
        # $HOME/.claude/.claude.json and miss the file below → "auto".
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        mode_env.write_legacy({"teammateMode": "in-process"})  # $HOME/.claude.json
        assert resolve_effective_teammate_mode() == "in-process"
