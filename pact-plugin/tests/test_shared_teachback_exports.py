"""
Tests for shared/__init__.py teachback gate exports (#401 Commit #2).

Verifies that TEACHBACK_STATES, the three threshold constants, the two
mode strings, and the three helper functions are importable from the
shared package root. Consumers (teachback_gate.py, task_schema_validator.py,
teachback_idle_guard.py) will write `from shared import ...` — any
missing export is a hard import error at hook startup.

Also asserts __all__ advertises every new name, so static tools
(IDE autocomplete, pyright) can see the public surface.
"""

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestTeachbackConstantsImportable:
    """Every teachback-gate constant must import from the package root."""

    def test_teachback_states_is_frozenset_of_four(self):
        from shared import TEACHBACK_STATES
        assert isinstance(TEACHBACK_STATES, frozenset)
        assert len(TEACHBACK_STATES) == 4

    def test_teachback_states_has_locked_names(self):
        """Drift guard — TERMINOLOGY-LOCK.md forbids any state rename.
        If this test changes, every protocol doc + test fixture must
        change in lockstep (see TERMINOLOGY-LOCK.md §Drift tests)."""
        from shared import TEACHBACK_STATES
        assert TEACHBACK_STATES == frozenset({
            "teachback_pending",
            "teachback_under_review",
            "active",
            "teachback_correcting",
        })

    def test_banned_state_names_absent(self):
        """Canonical-plan F12 names (superseded by tightening) must not
        appear in the locked set."""
        from shared import TEACHBACK_STATES
        for banned in ("teachback_awaiting_lead", "teachback_cleared",
                       "teachback_expired", "teachback_bypassed"):
            assert banned not in TEACHBACK_STATES

    def test_timeout_idle_count_is_three(self):
        from shared import TEACHBACK_TIMEOUT_IDLE_COUNT
        assert TEACHBACK_TIMEOUT_IDLE_COUNT == 3

    def test_blocking_threshold_is_seven(self):
        from shared import TEACHBACK_BLOCKING_THRESHOLD
        assert TEACHBACK_BLOCKING_THRESHOLD == 7

    def test_full_protocol_variety_is_nine(self):
        from shared import TEACHBACK_FULL_PROTOCOL_VARIETY
        assert TEACHBACK_FULL_PROTOCOL_VARIETY == 9

    def test_full_protocol_scope_items_is_two(self):
        from shared import TEACHBACK_FULL_PROTOCOL_SCOPE_ITEMS
        assert TEACHBACK_FULL_PROTOCOL_SCOPE_ITEMS == 2

    def test_mode_constants(self):
        from shared import TEACHBACK_MODE_BLOCKING, TEACHBACK_MODE_ADVISORY
        assert TEACHBACK_MODE_BLOCKING == "blocking"
        assert TEACHBACK_MODE_ADVISORY == "advisory"


class TestTeachbackFunctionsImportable:
    """Every teachback-gate helper must import from the package root."""

    def test_teachback_mode_for_score(self):
        from shared import teachback_mode_for_score
        assert teachback_mode_for_score(6) == "advisory"
        assert teachback_mode_for_score(7) == "blocking"

    def test_auditor_required_for_score(self):
        from shared import auditor_required_for_score
        assert auditor_required_for_score(6) is False
        assert auditor_required_for_score(7) is True

    def test_gates_for_score(self):
        from shared import gates_for_score
        result = gates_for_score(7)
        assert set(result.keys()) == {"teachback_mode", "auditor_required", "workflow_route"}


class TestSharedAllIntegrity:
    """The __all__ list advertises every teachback-gate export."""

    TEACHBACK_EXPORTS = (
        "TEACHBACK_STATES",
        "TEACHBACK_TIMEOUT_IDLE_COUNT",
        "TEACHBACK_BLOCKING_THRESHOLD",
        "TEACHBACK_FULL_PROTOCOL_VARIETY",
        "TEACHBACK_FULL_PROTOCOL_SCOPE_ITEMS",
        "TEACHBACK_MODE_BLOCKING",
        "TEACHBACK_MODE_ADVISORY",
        "teachback_mode_for_score",
        "auditor_required_for_score",
        "gates_for_score",
    )

    def test_all_teachback_exports_are_in_dunder_all(self):
        import shared
        for name in self.TEACHBACK_EXPORTS:
            assert name in shared.__all__, (
                f"shared.__all__ missing {name!r} — IDE/tooling autocomplete "
                f"will not surface the export"
            )

    def test_every_all_entry_resolves(self):
        """Negative-space test: every name in __all__ is actually defined
        on the module — catches broken re-exports or missing imports."""
        import shared
        for name in shared.__all__:
            assert hasattr(shared, name), f"__all__ names {name!r} but shared lacks attribute"


class TestSharedPackageImportClean:
    """Reloading the package must not raise; existing behavior preserved."""

    def test_reload_is_clean(self):
        """Module-load-time errors (e.g., circular import, missing file)
        surface via importlib.reload."""
        import shared
        importlib.reload(shared)
