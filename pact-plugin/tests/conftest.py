"""
Shared test fixtures and infrastructure.

This conftest.py is intentionally thin. It owns:
- sys.path setup for tests/, hooks/, skills/pact-memory/
- the genuinely cross-cutting ``pact_context`` fixture

Concern-specific helpers live in tests/fixtures/<concern>.py and are
imported directly by the test files that need them (direct-import
symbols); pytest-fixture-injected symbols would need a conftest
re-export to be discoverable, but none are currently defined there.
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


@pytest.fixture(autouse=True)
def _resync_staleness_resolver_bindings():
    """Unconditional cross-test isolation for the project-CLAUDE.md staleness
    resolver (#928). Runs for EVERY test (autouse).

    ROOT CAUSE (instrumented): a test elsewhere in the suite replaces
    ``sys.modules['staleness']`` with a fresh module object (e.g. a delete +
    re-import), ORPHANING the staleness functions that ``session_init`` bound at
    its own import time: ``session_init._staleness_check`` (==
    ``staleness.check_pinned_staleness``) and the re-imported
    ``session_init._get_project_claude_md_path`` keep pointing at the OLD
    staleness module's ``__dict__``. Then
    ``test_staleness.py::test_no_claude_md_returns_none`` patches the CURRENT
    ``staleness._get_project_claude_md_path`` to None, but
    ``session_init.check_pinned_staleness`` delegates to its orphaned
    ``_staleness_check``, which resolves ``_get_project_claude_md_path`` in the
    OLD module's globals (UNPATCHED) → reads the REAL project CLAUDE.md instead
    of None. Order-dependent + cumulative (needs session_init imported BEFORE the
    staleness replacement), so it only surfaces under certain suite orderings —
    a PRE-EXISTING latent defect, not specific to any one PR.

    FIX: before AND after every test, re-align ``session_init``'s bound staleness
    functions to whatever ``sys.modules['staleness']`` currently is (NOT a value
    captured at conftest-import, which would itself be the stale module). This is
    a no-op in the normal case (the bindings already match) and repairs the
    orphaning after a replacement, so a test that patches ``staleness`` reaches
    the resolution ``session_init`` actually uses. Also re-point the underscore
    alias to the current public resolver so the two never diverge within a
    module. Gated on ``sys.modules`` so this fixture never forces the heavy
    ``session_init`` import itself.
    """
    def _resync():
        st = sys.modules.get("staleness")
        si = sys.modules.get("session_init")
        if st is not None:
            real = getattr(st, "get_project_claude_md_path", None)
            if real is not None:
                st._get_project_claude_md_path = real
                if si is not None:
                    si._get_project_claude_md_path = real
        if st is not None and si is not None:
            if hasattr(st, "check_pinned_staleness"):
                si._staleness_check = st.check_pinned_staleness
            if hasattr(st, "check_pinned_block_signal"):
                si._staleness_block_check = st.check_pinned_block_signal

    _resync()
    yield
    _resync()


@pytest.fixture(autouse=True)
def _restore_claude_project_dir_env():
    """Snapshot + restore ``os.environ['CLAUDE_PROJECT_DIR']`` around every test
    (#930). Runs for EVERY test (autouse).

    Some concurrency tests (test_working_memory_concurrency*.py) set
    ``os.environ['CLAUDE_PROJECT_DIR']`` via DIRECT assignment (NOT
    ``monkeypatch.setenv``), so it is never restored and LEAKS into later tests
    — an order-dependent pollution vector. A leaked ``CLAUDE_PROJECT_DIR``
    redirects ``CLAUDE_PROJECT_DIR``-keyed resolvers (e.g.
    ``staleness.get_project_claude_md_path``) away from the test's intended root,
    so a later test silently resolves the wrong project dir. ``monkeypatch``-
    based env tests are immune (auto-revert); the leak is the direct-assignment
    ones specifically.

    This fixture SNAPSHOTS the var at setup and RESTORES it at teardown
    (set-to-original, or DELETE if it was originally unset) — an unconditional
    restore, immune to dirty-baseline chains, and a no-op for the vast majority
    of tests that never touch it. Co-located with the other env/module-state
    autouse resets (``_reset_pact_context_state`` / ``_reset_specialist_registry_cache``
    / ``_resync_staleness_resolver_bindings``). CLAUDE_PROJECT_DIR is the
    CONFIRMED leaker (the only ``CLAUDE_*`` the working_memory tests set); if a
    sibling direct-assignment leak is ever confirmed (e.g. ``CLAUDE_PLUGIN_ROOT``
    in test_plugin_manifest.py), generalize this snapshot to the ``CLAUDE_*``
    namespace.
    """
    _UNSET = object()
    original = os.environ.get("CLAUDE_PROJECT_DIR", _UNSET)
    yield
    if original is _UNSET:
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
    else:
        os.environ["CLAUDE_PROJECT_DIR"] = original


@pytest.fixture(autouse=True)
def _scrub_claude_plugin_root_env():
    """Pop + restore ``os.environ['CLAUDE_PLUGIN_ROOT']`` around every test.
    Runs for EVERY test (autouse).

    ``shared.pact_context.get_plugin_root()`` falls back to the
    CLAUDE_PLUGIN_ROOT env var when the context-file value is empty or the
    file is missing. That fallback makes ambient process env VISIBLE to
    production code under test: running the suite inside an environment
    that exports CLAUDE_PLUGIN_ROOT (e.g. a Claude Code hook process, or a
    developer shell that sourced one) would silently flip the
    empty-plugin_root pins (test_pact_context.py
    ``test_returns_empty_when_plugin_root_missing``, test_bootstrap_gate.py
    ``test_rejects_when_plugin_root_missing``) from deterministic to
    environment-sensitive.

    Unlike the CLAUDE_PROJECT_DIR sibling above (snapshot/restore only —
    guarding cross-test LEAKS), this fixture POPS the var at setup so every
    test starts from a guaranteed-unset baseline, then restores the original
    value at teardown. Tests that exercise the fallback set the var
    explicitly via monkeypatch.setenv. This is the generalization the
    sibling fixture's closing comment anticipated for CLAUDE_PLUGIN_ROOT.
    """
    _UNSET = object()
    original = os.environ.pop("CLAUDE_PLUGIN_ROOT", _UNSET)
    yield
    if original is _UNSET:
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
    else:
        os.environ["CLAUDE_PLUGIN_ROOT"] = original


@pytest.fixture(autouse=True)
def _isolate_config_root_to_tmp(tmp_path, monkeypatch):
    """Redirect the Claude Code config/state root to a per-test tmp tree for
    EVERY test (autouse, opt-OUT), via TWO mechanisms: scrub any inherited
    CLAUDE_CONFIG_DIR, then redirect ``Path.home()`` -> tmp_path. Runs for EVERY
    test.

    ``get_claude_config_dir()`` (hooks/shared/paths.py) — the single source of
    truth every PACT state writer resolves through — honors ``$CLAUDE_CONFIG_DIR``
    FIRST (precedence-1), falling back to ``Path.home() / ".claude"`` ONLY when
    the var is unset. The two mechanisms make the redirect DETERMINISTIC
    regardless of contributor env:

    1. SCRUB CLAUDE_CONFIG_DIR at setup (POP any inherited value; restore the
       original at teardown) — same posture as the sibling
       ``_scrub_claude_plugin_root_env``. This makes the HOME fallthrough the
       LIVE resolution path. A contributor who exports CLAUDE_CONFIG_DIR (their
       real config) must NOT leak through — exactly the "forgetting is the
       default" gap #1186 exists to close.
    2. REDIRECT ``Path.home()`` -> tmp_path (``monkeypatch.setattr``, matching
       the suite's own isolation convention — e.g. test_artifact_paths_durability
       and the teammate_mode ``_ModeEnv`` harness). In-process,
       ``get_claude_config_dir()`` now resolves to ``tmp_path / ".claude"``, so
       every SSOT-respecting in-process writer (``agent_handoff_marker._marker_dir``
       = ``get_claude_config_dir() / "teams" / team / namespace``; the registry
       reaper's default root; etc.) lands under the per-test tmp, NEVER the
       operator's real ``~/.claude``. This closes #1186's destructive leak (the
       marker writer is in-process).

    WHY NOT ALSO SET ``HOME`` ENV (deliberate): ``monkeypatch.setattr`` does not
    cross to subprocesses, so a subprocess test that needs its child to resolve
    the SAME tmp root must set the env var ITSELF in-body (the established
    pattern — see test_pact_harvest_cli's subprocess test that does
    ``monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))``
    alongside its Path.home patch). A GLOBAL ``HOME`` env override here was
    tried and REJECTED: it breaks the telegram tests
    (``test_*_cwd_is_home``), which capture ``home = os.path.expanduser("~")``,
    then ``patch.dict(os.environ, {}, clear=True)`` and assert the cwd-is-home
    branch — under a ``HOME`` override the captured value is tmp but the
    cleared-env ``expanduser`` falls back to the real pwd-home, so cwd no longer
    equals home. A global ``HOME`` override trades ~3 legitimate tests for ~4
    accidentally-consistent subprocess tests — a bad trade. Subprocess
    hermeticity is per-test (the in-body env pattern), not a fixture
    responsibility; residual non-isolated subprocess tests surface as findings
    for per-test fixes, not as a fixture gap. #1186's destructive leak
    (in-process marker writes) is fully closed by the setattr alone.

    CRITICAL — DO NOT SET CLAUDE_CONFIG_DIR HERE. Precedence-1 makes it shadow
    ``Path.home()`` entirely. The suite's pervasive isolation convention patches
    ``Path.home()`` (-> a tmp) and relies on the HOME fallthrough being ACTIVE;
    setting CLAUDE_CONFIG_DIR defeated that convention and broke ~336 tests (all
    HOME-patchers) while helping none. The SCRUB keeps the fallthrough live (it
    UNSETS the var); tests that genuinely need a specific config root set
    CLAUDE_CONFIG_DIR THEMSELVES in-body, and precedence-1 then overrides the
    scrub for that one test. Verified non-vacuity tests (e.g. test_task_utils
    ``TestReadTaskJsonResolverEnvSet``: sets CLAUDE_CONFIG_DIR + redirects HOME
    to an EMPTY dir to certify the env-set path) pass because their in-body
    patches override BOTH this fixture's scrub and HOME-redirect.

    Concrete leak this guards (#1186): the sibling autouse fixtures snapshot
    CLAUDE_PROJECT_DIR and scrub CLAUDE_PLUGIN_ROOT but redirect NEITHER HOME
    nor CLAUDE_CONFIG_DIR — the var (and its HOME fallback) governing every
    destructive path. ``agent_handoff_marker._marker_dir`` resolved
    ``get_claude_config_dir() / "teams" / team_name`` internally with no seam, so
    a test exercising the marker writer against the live resolver wrote
    test-fixture marker dirs into the operator's real ``~/.claude/teams/`` since
    ~2026-04-05. This fixture inverts opt-in to opt-OUT: every test gets a
    throwaway ``tmp_path / ".claude"`` root regardless of ambient env.

    A test that ONLY passed against the real ``~/.claude`` (or the operator's
    ambient ``CLAUDE_CONFIG_DIR``) was never isolated — under this fixture it
    surfaces as a failure, which IS the finding: fix the test to be isolated,
    never by re-pointing at the real home, weakening this redirect, or widening
    any reaper. ``Path.home`` is patched in-process (what
    ``get_claude_config_dir`` calls); subprocess tests that need their child to
    resolve tmp set the env var themselves in-body (see "WHY NOT ALSO SET HOME"
    above).
    """
    _UNSET = object()
    original_cfg = os.environ.pop("CLAUDE_CONFIG_DIR", _UNSET)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield
    if original_cfg is _UNSET:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
    else:
        os.environ["CLAUDE_CONFIG_DIR"] = original_cfg
