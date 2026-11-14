"""C2 equivalence tests: routing the two *_MODE gate reads through
shared.pact_config.get_enum must preserve the pre-refactor resolved mode
EXACTLY at BOTH gate sites.

Before C2, dispatch_gate.py and handoff_ordering_gate.py each resolved their
mode inline as:  os.environ.get(NAME, DEFAULT).strip().lower()  then a frozenset
membership check falling back to DEFAULT. `_old_resolve` below is that exact
logic, used as the oracle. The acceptance bar is: for every input in the battery
(valid values, invalid, whitespace, mixed case, unset), the new resolution ==
the old resolution.

SCOPE OF THE EQUIVALENCE CLAIM, and it is narrower than it first reads: what
C2 had to preserve is the RESOLUTION MECHANISM -- normalization, membership,
and fall-back-to-the-declared-default. It is NOT a claim that a given option's
default never changes. The two options now declare DIFFERENT defaults on
purpose (`_DEFAULT` below), so the oracle is parameterised by the declared
default rather than pinning one literal for both; pinning one would turn a
deliberate policy change into a red test, which is the shape that gets a policy
reverted to keep a suite green.

Two layers:
- TestResolverValueEquivalence drives pact_config.get_enum directly (full
  input coverage, cheap).
- TestGateSiteWiring reloads the REAL gate modules under each input and reads
  their module-level mode constant -- proving the RHS swap is actually wired
  (catches a composed-but-unwired resolver or a wrong env-var name), not merely
  that get_enum is correct in isolation.
"""
import importlib

import pytest

import shared.pact_config as pact_config
from fixtures.gate_reload import restored_env_and_reloaded


_ALLOWED = frozenset({"warn", "deny", "shadow"})

INLINE = "PACT_DISPATCH_INLINE_MISSION_MODE"
VARIETY = "PACT_DISPATCH_VARIETY_MODE"

# The battery: unset (None), all valid values, an invalid value, whitespace, and
# mixed case -- the inputs whose resolution the RHS swap must not perturb.
_BATTERY = [None, "warn", "deny", "shadow", "banana", "  deny ", "DENY", "", "SHADOW", "Deny"]

# Each option's SHIPPED default, written here as an independent literal rather
# than read back from the registry -- reading the registry would make every
# unset/invalid row tautological. The dispatch-variety gate ships ENFORCING;
# the inline-mission gate ships advisory. A revert of either default reddens
# the unset and invalid rows below, which is the point.
_DEFAULT = {INLINE: "warn", VARIETY: "deny"}

# Where an UNPARSEABLE value lands, which is NOT always where an unset one does.
# The variety row declares `invalid_fallback = "warn"` against a `deny` default;
# the inline row declares none, so its two paths coincide. Splitting the oracle
# is required rather than cosmetic: a single-default oracle cannot express a row
# whose unset and invalid paths differ, and would silently assert the collapse
# the declaration exists to prevent.
_INVALID_FALLBACK = {INLINE: "warn", VARIETY: "warn"}


def _old_resolve(raw, default="warn", invalid_fallback=None):
    """The pre-refactor inline logic BOTH gates used, generalised over the
    option's declared default AND its declared invalid fallback.

    os.environ.get(NAME) -> unset yields `default`; otherwise strip().lower()
    and, if the value is outside the allowed set (including an empty string or
    a bogus token), yield `invalid_fallback` (which defaults to `default`, the
    behaviour of every row that declares no fallback).
    """
    if invalid_fallback is None:
        invalid_fallback = default
    if raw is None:
        return default
    value = raw.strip().lower()
    return value if value in _ALLOWED else invalid_fallback


@pytest.fixture(scope="module", autouse=True)
def _restore_gate_modules():
    """After this file's tests, restore both mode variables to the values they
    had before it ran (set or absent) and reload both gate modules, so a later
    test file sees the constants it saw before this module. We must NOT evict
    them from sys.modules -- sibling files import them at collection time and
    call importlib.reload, which raises if the module is missing from
    sys.modules."""
    with restored_env_and_reloaded(
        (INLINE, VARIETY), ("dispatch_gate", "handoff_ordering_gate")
    ):
        yield


@pytest.fixture
def clean(monkeypatch):
    """Reset both env vars for a test. Does NOT touch sys.modules (see
    _restore_gate_modules for why eviction breaks sibling test files)."""
    for name in (INLINE, VARIETY):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestResolverValueEquivalence:
    """get_enum reproduces the pre-refactor inline mode resolution exactly."""

    @pytest.mark.parametrize("name", [INLINE, VARIETY])
    @pytest.mark.parametrize("raw", _BATTERY)
    def test_get_enum_matches_old_inline_logic(self, clean, name, raw):
        if raw is None:
            clean.delenv(name, raising=False)
        else:
            clean.setenv(name, raw)
        assert pact_config.get_enum(name) == _old_resolve(raw, _DEFAULT[name], _INVALID_FALLBACK[name])


def _reload_gate_mode(monkeypatch, module_name, env_name, raw, const_name):
    """Set env, reload the gate module (keeping it in sys.modules), and return
    its resolved-mode constant."""
    if raw is None:
        monkeypatch.delenv(env_name, raising=False)
    else:
        monkeypatch.setenv(env_name, raw)
    module = importlib.import_module(module_name)
    importlib.reload(module)
    return getattr(module, const_name)


class TestGateSiteWiring:
    """The real gate modules' mode constants reflect the resolver across the
    battery -- proves the swap is wired at each site, not just correct in the
    resolver."""

    @pytest.mark.parametrize("raw", _BATTERY)
    def test_dispatch_gate_inline_mission_mode(self, clean, raw):
        got = _reload_gate_mode(clean, "dispatch_gate", INLINE, raw, "INLINE_MISSION_MODE")
        assert got == _old_resolve(raw, _DEFAULT[INLINE], _INVALID_FALLBACK[INLINE])

    @pytest.mark.parametrize("raw", _BATTERY)
    def test_handoff_gate_variety_mode(self, clean, raw):
        got = _reload_gate_mode(
            clean, "handoff_ordering_gate", VARIETY, raw, "DISPATCH_VARIETY_MODE"
        )
        assert got == _old_resolve(raw, _DEFAULT[VARIETY], _INVALID_FALLBACK[VARIETY])
