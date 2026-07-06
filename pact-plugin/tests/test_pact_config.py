"""Tests for shared/pact_config.py -- the os.environ-blind PACT_* resolver.

The resolver is the single source of truth for PACT runtime options. It is
fail-open by construction (every public function is total; any failure returns
the registry default) and os.environ-BLIND with CALL-TIME reads (zero work at
import, no caching -- so a consumer that resolves at its own module load sees
the same value a direct os.environ.get would, and these tests can monkeypatch
os.environ without importlib.reload).

Core invariants under test:
- bool parse is EXACT-MEMBERSHIP, never Python truthiness: "0"/"2"/"maybe" ->
  False (the fail-SAFE direction; a garbled flag stays OFF). bool("0")==True
  would be the F2 fail-unsafe slip this guards against.
- enum parse replicates the gates' .strip().lower() normalization; an unset var
  -> silent default; a SET-but-invalid value -> default + a stderr WARN (the
  non-vacuity tell that the invalid branch is live).
- llm_options() surfaces ONLY consumer=="llm" options as typed values.
- fail-safe: unknown option + an internal exception both resolve to the default
  without raising.
- call-time read: setting an env var AFTER import still changes the result
  (proves no import-time caching).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import shared.pact_config as pact_config
from shared.pact_config import get_bool, get_enum, llm_options


_GREEDY = "PACT_PR_GREEDY_FIX"
_AUTO = "PACT_AUTONOMOUS_SCOPE_DETECTION"
_INLINE = "PACT_DISPATCH_INLINE_MISSION_MODE"
_VARIETY = "PACT_DISPATCH_VARIETY_MODE"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every test from an unset state for all registered options."""
    for name in (_GREEDY, _AUTO, _INLINE, _VARIETY):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestGetBoolExactMembership:
    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "TRUE", "Yes", " on ", "ON"])
    def test_true_tokens_resolve_true(self, _clean_env, raw):
        _clean_env.setenv(_GREEDY, raw)
        assert get_bool(_GREEDY) is True

    @pytest.mark.parametrize("raw", ["0", "2", "maybe", "false", "no", "off", "", "  ", "enabled", "y"])
    def test_everything_else_resolves_false(self, _clean_env, raw):
        # Includes "0" -- the F2 anchor: Python truthiness (bool("0")==True)
        # would fail UNSAFE here; exact-membership keeps it OFF.
        _clean_env.setenv(_GREEDY, raw)
        assert get_bool(_GREEDY) is False

    def test_unset_returns_default_false(self, _clean_env):
        assert get_bool(_GREEDY) is False
        assert get_bool(_AUTO) is False


class TestGetEnumNormalizationAndValidation:
    @pytest.mark.parametrize("raw,expected", [
        ("warn", "warn"), ("deny", "deny"), ("shadow", "shadow"),
        ("DENY", "deny"), (" deny ", "deny"), ("Warn", "warn"), ("SHADOW", "shadow"),
    ])
    def test_valid_values_normalized(self, _clean_env, raw, expected):
        _clean_env.setenv(_INLINE, raw)
        assert get_enum(_INLINE) == expected

    def test_unset_returns_default_silently(self, _clean_env, capsys):
        assert get_enum(_INLINE) == "warn"
        assert get_enum(_VARIETY) == "warn"
        # Unset is the steady state -- NOT a misconfiguration -> no warning.
        assert capsys.readouterr().err == ""

    def test_invalid_value_falls_back_and_warns(self, _clean_env, capsys):
        _clean_env.setenv(_VARIETY, "banana")
        assert get_enum(_VARIETY) == "warn"
        # The non-vacuity tell: the invalid branch MUST emit a stderr warning.
        err = capsys.readouterr().err
        assert "PACT_DISPATCH_VARIETY_MODE" in err
        assert "banana" in err


class TestLlmOptions:
    def test_defaults_all_off(self, _clean_env):
        assert llm_options() == {_GREEDY: False, _AUTO: False}

    def test_reflects_resolved_values(self, _clean_env):
        _clean_env.setenv(_GREEDY, "1")
        assert llm_options() == {_GREEDY: True, _AUTO: False}

    def test_excludes_hook_consumer_options(self, _clean_env):
        # get_enum options (consumer=="hook") must NOT appear in the LLM payload.
        _clean_env.setenv(_INLINE, "deny")
        _clean_env.setenv(_VARIETY, "shadow")
        keys = set(llm_options().keys())
        assert _INLINE not in keys
        assert _VARIETY not in keys
        assert keys == {_GREEDY, _AUTO}


class TestFailSafe:
    def test_unknown_option_returns_safe_defaults(self, _clean_env):
        # Unknown options are not consumed; resolve to the type-neutral safe
        # default without raising.
        assert get_bool("PACT_DOES_NOT_EXIST") is False
        assert get_enum("PACT_DOES_NOT_EXIST") == ""

    def test_internal_exception_falls_back_to_default(self, _clean_env, monkeypatch):
        # Force the parse to raise; the total contract must swallow it and
        # return the registry default (never propagate).
        def _boom(_raw):
            raise RuntimeError("normalization blew up")

        monkeypatch.setattr(pact_config, "_normalize", _boom)
        _clean_env.setenv(_GREEDY, "1")
        _clean_env.setenv(_INLINE, "deny")
        assert get_bool(_GREEDY) is False   # bool default
        assert get_enum(_INLINE) == "warn"  # enum default


class TestCallTimeReadNoCaching:
    def test_env_set_after_import_is_observed(self, _clean_env):
        # pact_config was imported at module top; setting the var now must still
        # change the result -> proves LIVE per-call reads, not import caching.
        assert get_bool(_GREEDY) is False
        _clean_env.setenv(_GREEDY, "1")
        assert get_bool(_GREEDY) is True
