"""
Shared test fixtures and infrastructure.

This conftest.py is intentionally thin. It owns:
- sys.path setup for tests/, hooks/, skills/pact-memory/
- the genuinely cross-cutting ``pact_context`` fixture
- re-exports of pytest-fixture-injected symbols from tests/fixtures/

Concern-specific helpers live in tests/fixtures/<concern>.py and are
imported directly by the test files that need them (direct-import
symbols) or re-exported here (pytest-fixture-injected symbols).
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Add tests directory to path for helpers + fixtures module imports
sys.path.insert(0, str(Path(__file__).parent))

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

# Add pact-memory scripts to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'pact-memory'))

# Re-exports for pytest-fixture-injection.
# Pytest discovers fixtures by name in conftest.py; these re-exports
# make subdir-defined fixtures injectable in any test_*.py file.
from fixtures.refresh_system import (  # noqa: E402, F401
    tmp_transcript,
    sample_checkpoint,
    peer_review_mid_workflow_transcript,
    orchestrate_code_phase_transcript,
    no_workflow_transcript,
    terminated_workflow_transcript,
)


@pytest.fixture
def pact_context(tmp_path, monkeypatch):
    """
    Factory fixture to create a mock PACT session context file for testing.

    Creates a temporary context file and patches _context_path
    so hooks read from it instead of the real session-scoped location.

    Usage:
        def test_something(pact_context):
            pact_context(team_name="test-team", session_id="test-session")
            # Now get_team_name() returns "test-team", etc.

    Returns:
        Function that writes a context file and returns its path
    """
    import shared.pact_context as ctx_module

    context_file = tmp_path / "pact-session-context.json"

    def _write(
        team_name="test-team",
        session_id="test-session",
        project_dir="/test/project",
        plugin_root="",
        started_at="2026-01-01T00:00:00Z",
    ):
        context_file.write_text(json.dumps({
            "team_name": team_name,
            "session_id": session_id,
            "project_dir": project_dir,
            "plugin_root": plugin_root,
            "started_at": started_at,
        }), encoding="utf-8")
        # Patch the resolved context path to point to our test file
        monkeypatch.setattr(ctx_module, "_context_path", context_file)
        # Clear the module-level cache so fresh reads happen
        monkeypatch.setattr(ctx_module, "_cache", None)
        return context_file

    # Always reset module state at fixture setup (even if _write isn't called,
    # ensures no cross-test cache or path leakage)
    monkeypatch.setattr(ctx_module, "_cache", None)
    monkeypatch.setattr(ctx_module, "_context_path", None)

    return _write


@pytest.fixture(autouse=True)
def _reset_pact_context_state():
    """Unconditional cross-test isolation for shared.pact_context's
    module-global session cache. Runs for EVERY test (autouse).

    ``shared.pact_context`` memoizes the resolved session context in two
    module-level globals — ``_cache`` (the parsed context dict) and
    ``_context_path`` (the resolved context-file path), both defaulting to
    ``None`` at import. They are populated lazily on the first
    ``get_session_id()`` / ``get_team_name()`` call and then persist for the
    life of the process. That is correct in production (one session per
    process) but a cross-test LEAK under pytest, where a single process is
    reused across the whole suite: a test that populates the cache with a
    non-empty session (directly, or via the opt-in ``pact_context`` fixture)
    leaves it dirty for every later test in the run.

    Concrete failure this guards (the #845 order-dependent break): merge_guard's
    ``find_valid_token`` calls ``get_session_id()``; when the session is
    non-empty it rejects authorization tokens that carry no/mismatched
    ``session_id``. merge_guard's own tests write SESSIONLESS tokens and rely on
    the empty-session graceful-degradation path, so a leaked session from an
    upstream test made ~19 merge_guard tests fail under the full suite while
    passing standalone.

    This fixture force-resets that state to its import-time clean default
    before AND after every test by calling the module's own public
    ``reset_for_tests()`` hook (an unconditional reset, NOT a ``monkeypatch``
    revert, so it is immune to dirty-baseline chains where a polluting test's
    own ``monkeypatch.setattr(..., None)`` records an already-polluted value
    and "reverts" to it). Delegating to ``pact_context.reset_for_tests()``
    (rather than direct-assigning the private ``_cache`` / ``_context_path``
    globals here) keeps the reset co-located with the module that owns the
    state: a future rename of those internals updates the reset hook in the
    same module, instead of silently turning this fixture into a no-op. This
    autouse reset is the single source of truth for cross-test isolation; the
    opt-in ``pact_context`` fixture above layers on top of it to CONFIGURE a
    context for tests that need a populated session.
    """
    import shared.pact_context as ctx_module

    ctx_module.reset_for_tests()
    yield
    ctx_module.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_specialist_registry_cache():
    """Unconditional cross-test isolation for ``dispatch_helpers``'s
    ``_specialist_registry`` ``@lru_cache``. Runs for EVERY test (autouse).

    F1 (#883 fold-in). ``_specialist_registry`` memoizes the globbed
    ``agents/pact-*.md`` registry (keyed on the pact_context-resolved
    ``plugin_root``) for the life of the process. Like ``pact_context``'s
    ``_cache`` / ``_context_path`` (reset by the sibling fixture above), an
    uncleared lru_cache leaks a stale — or empty — registry across tests: the
    same #845-class order-dependent pollution vector, now more exposed since the
    suite exercises the registry more (the is_lead startup-notice path + the
    dispatch tests). Cleared before AND after every test (symmetric), mirroring
    the pact_context reset.

    DELIBERATELY A SEPARATE FIXTURE (SRP): the sibling
    ``_reset_pact_context_state`` is the documented single-source-of-truth for
    ``pact_context``'s module-globals; folding a second module's cache-clear into
    it would muddy that scope. Each autouse reset owns exactly one module's
    state. (``cache_clear`` is the lru_cache-provided reset; a future removal of
    the ``@lru_cache`` decorator on ``_specialist_registry`` must update this.)
    """
    import shared.dispatch_helpers as dispatch_helpers

    dispatch_helpers._specialist_registry.cache_clear()
    yield
    dispatch_helpers._specialist_registry.cache_clear()
