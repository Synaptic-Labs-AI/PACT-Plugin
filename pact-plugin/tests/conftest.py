"""
Shared test fixtures and infrastructure.

This conftest.py is intentionally thin. It owns:
- sys.path setup for tests/, hooks/, skills/pact-memory/ (and its scripts/
  dir), skills/pact-coding-standards/scripts/, and plugin-level scripts/
- the genuinely cross-cutting ``pact_context`` fixture

Concern-specific helpers live in tests/fixtures/<concern>.py and are
imported directly by the test files that need them (direct-import
symbols); pytest-fixture-injected symbols would need a conftest
re-export to be discoverable, but none are currently defined there.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Add tests directory to path for helpers + fixtures module imports
sys.path.insert(0, str(Path(__file__).parent))

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

# Add pact-memory scripts to path (normalized: package-route imports build
# module __file__ from this entry, and instruments that string-compare
# __file__/co_filename against a resolved path break on a literal '..')
sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "pact-memory"))

# New-skill scripts coverage is owned by the plugin-root conftest's
# skills/*/scripts glob; the two explicit skill-scripts lines below remain as belt.

# Add the pact-memory scripts dir itself to path (bare-module imports)
sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts"))

# Add pact-coding-standards scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "pact-coding-standards" / "scripts"))

# Add plugin-level scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

# The clock-shift census; tests/ is on sys.path from the insert above.
from clock_shift import clock_shift_census  # noqa: E402

# These copy functions from other shared modules at first import; loading them here keeps a test patch from being copied.
import shared.background_work  # noqa: E402, F401
import shared.task_metadata_snapshot  # noqa: E402, F401
import shared.task_utils  # noqa: E402, F401


# Name of the environment variable that relocates the PACT memory store.
#
# DUPLICATED FROM `scripts.config.MEMORY_DIR_ENV` ON PURPOSE. Importing it here
# would make the whole suite depend on the `scripts` package importing cleanly
# during conftest load, which turns a missing optional dependency into a total
# collection failure. The literal is pinned against its source by
# test_memory_store_isolation.py, so a rename fails loudly instead of silently
# un-isolating every test.
_MEMORY_DIR_ENV = "PACT_TEST_MEMORY_DIR"

# Session-wide fallback store, created once and reused. See pytest_configure.
_SESSION_MEMORY_DIR = os.path.join(
    tempfile.mkdtemp(prefix="pact-memory-suite-"), "pact-memory"
)


def pytest_configure(config):
    """Register markers that DECLARE an external dependency.

    A test whose pass depends on something outside the repo must say so.
    An undeclared dependency does not fail as a named condition — it fails
    as flakiness, in an environment nobody was watching, which is the same
    "reports success while measuring nothing" defect read backwards.

    These markers deliberately do NOT skip. The tests run everywhere by
    default; the marker exists so a constrained environment can DESELECT
    them explicitly (`-m 'not requires_embedding_backend'`) and so the
    deselection appears in the pytest header rather than as an anonymous
    skip. Skipping by default would hide the dependency inside the skip
    count, where a reviewer reading "N skipped" cannot tell an intentional
    environmental exclusion from a test quietly disabled to buy a green.
    """
    config.addinivalue_line(
        "markers",
        "requires_embedding_backend: exercises the real pact-memory CLI "
        "save path, which spins the embedding backend and reads its model "
        "from a local cache. On a COLD cache that read is a network "
        "download — deselect in a sandboxed or offline environment.",
    )

    # RELOCATE THE MEMORY STORE BEFORE COLLECTION BEGINS.
    #
    # This runs before any test module is imported, which is the property that
    # matters and the one no fixture can offer. A fixture runs per test, so a
    # module-level `from scripts.memory_api import ...` executed at COLLECTION
    # time would resolve the store before any fixture had a chance to redirect
    # it. Setting the variable here means every import, every fixture and every
    # spawned child sees the redirected store from the first instruction.
    #
    # It is also the only mechanism that CROSSES A PROCESS BOUNDARY. The suite's
    # `Path.home()` redirect cannot: a spawned CLI child is a fresh interpreter
    # in which that patch never happened, and the subprocess tests are the
    # larger half of the exposure this closes.
    #
    # DEFENCE IN DEPTH, NOT THE ONLY LAYER. `_isolate_memory_store_to_tmp`
    # overrides this per test with a `tmp_path` store. This one is the floor:
    # it catches anything that runs outside a fixture's reach.
    #
    # UNCONDITIONAL, NEVER `setdefault`. A contributor who has relocated their
    # own store already exports this variable, and honouring an inherited value
    # would point the whole suite at the very store this exists to protect. The
    # fail direction of `setdefault` here is ALLOW, and it is silent.
    os.environ[_MEMORY_DIR_ENV] = _SESSION_MEMORY_DIR

    # CLOCK-SHIFT CENSUS. Inert unless PACT_TEST_CLOCK_SHIFT_SECONDS is set; the
    # sweep command is in tests/clock_shift/clock_shift_shim.py.
    clock_shift_census.install()


def pytest_sessionfinish(session):
    """Fail a clock-shift sweep in which a child ran on the real clock."""
    clock_shift_census.finish(session)


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
def _scrub_claude_project_dir_env():
    """Pop + restore ``os.environ['CLAUDE_PROJECT_DIR']`` around every test.
    Runs for EVERY test (autouse).

    Two hazards, one variable:

    1. CROSS-TEST LEAK (#930). Some concurrency tests
       (test_working_memory_concurrency*.py) set ``CLAUDE_PROJECT_DIR`` via
       DIRECT assignment (NOT ``monkeypatch.setenv``), so it is never restored
       and LEAKS into later tests — an order-dependent pollution vector that
       redirects ``CLAUDE_PROJECT_DIR``-keyed resolvers (e.g.
       ``staleness.get_project_claude_md_path``) away from the test's intended
       root. The setup-time POP makes every test start from a
       guaranteed-unset baseline regardless of what an earlier test left
       behind — strictly stronger than the snapshot/restore-only posture this
       fixture used before (a leaked value could survive into the next test's
       setup when the snapshot recorded it).

    2. AMBIENT-ENV LEAK (the machine-state family). ``CLAUDE_PROJECT_DIR`` is
       a LIVE input to production code under test: ``pact_context.init()``
       derives the session context path from it, and
       ``backlog.project_root()`` anchors project resolution on it (NOT the
       process cwd). Running the suite inside a shell that exports the var
       (a Claude Code hook process, or a developer shell that sourced one)
       therefore flips environment-sensitive tests from deterministic to
       machine-dependent: measured as three pre-existing red failures on dev
       machines that are green on CI (where the var is unset) —
       test_backlog's "refusal: no root" exit-code arm (reconcile
       unexpectedly succeeds against the real repo), and both
       test_bootstrap_prompt_gate no-session tests (init() re-derives a
       path the test's ``_context_path = None`` patch cannot prevent, and
       the heal path can then WRITE a context file through the live root).
       Same posture as the sibling ``_scrub_claude_plugin_root_env`` /
       ``_scrub_claude_env_file_env``: POP at setup, restore the original
       ambient value at teardown. Tests that exercise the var set it
       explicitly via ``monkeypatch.setenv`` (which overrides the scrub for
       that test); the direct-assignment concurrency tests are unaffected
       (they set it after setup, inside the test body).

    Co-located with the other env/module-state autouse resets
    (``_reset_pact_context_state`` / ``_reset_specialist_registry_cache`` /
    ``_resync_staleness_resolver_bindings``).
    """
    _UNSET = object()
    original = os.environ.pop("CLAUDE_PROJECT_DIR", _UNSET)
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
def _scrub_claude_env_file_env():
    """Pop + restore ``os.environ['CLAUDE_ENV_FILE']`` around every test.
    Runs for EVERY test (autouse).

    session_init.main() reads ``CLAUDE_ENV_FILE`` and APPENDS an
    ``export CLAUDE_PROJECT_DIR=...`` line to it whenever the variable and
    ``CLAUDE_PROJECT_DIR`` are both present. A suite running inside a real
    hook process (or a developer shell that sourced one) carries a LIVE
    session env file in the environment, and every main()-driving test that
    sets ``CLAUDE_PROJECT_DIR`` would append a bogus export line to that live
    file — the same ambient-env hazard class ``_scrub_claude_plugin_root_env``
    closes. Same posture: POP at setup so every test starts from a
    guaranteed-unset baseline, restore the original at teardown. Tests that
    exercise the env-file channel set the variable explicitly via
    ``monkeypatch.setenv`` (which overrides the scrub for that test).
    """
    _UNSET = object()
    original = os.environ.pop("CLAUDE_ENV_FILE", _UNSET)
    yield
    if original is _UNSET:
        os.environ.pop("CLAUDE_ENV_FILE", None)
    else:
        os.environ["CLAUDE_ENV_FILE"] = original


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


@pytest.fixture(autouse=True)
def _isolate_memory_store_to_tmp(tmp_path, monkeypatch):
    """Redirect the PACT memory DATABASE to a per-test tmp tree for EVERY test.

    DELIBERATELY A SEPARATE FIXTURE from `_isolate_config_root_to_tmp`, matching
    the one-module-per-reset convention its siblings follow.

    WHICH SUBTREE EACH ONE REACHES, because neither owns the store root alone.
    This fixture sets `PACT_TEST_MEMORY_DIR`, which governs
    `config.get_memory_dir()` and therefore the DATABASE. The sibling patches
    `Path.home()` and clears `CLAUDE_CONFIG_DIR`, and that is what moves
    `session-tracking/` -- written by `hooks/track_files.py` through
    `get_claude_config_dir()` -- even though that subtree lives INSIDE the same
    root. Covering the whole tree takes BOTH, and they redirect through
    DIFFERENT mechanisms because they have to.

    WHY AN ENVIRONMENT VARIABLE AND NOT A `Path.home()` PATCH. The sibling's
    `monkeypatch.setattr(Path, "home", ...)` is in-process only, and it was
    measured insufficient for this store on BOTH axes:

      1. It does not cross a process boundary. A spawned CLI child is a fresh
         interpreter in which the patch never happened, so it resolved the real
         home. The subprocess tests were the larger half of the exposure.
      2. `scripts.config` used to bind its paths AT IMPORT, so the patch reached
         them only when that module first imported INSIDE a test. Any
         module-level `from scripts.memory_api import ...` imports it at
         COLLECTION time instead, and the redirect then reached nothing. The
         protection was real, accidental, and decided by import order.

    `scripts.config` now resolves at use time, which fixes (2) on its own. The
    variable is what fixes (1), because a child inherits the environment and
    nothing else survives the boundary.

    SCOPE: this is a redirect, not a refusal. Production is unaffected, since
    nothing sets the variable there and the resolver falls back to the real
    home exactly as before. Any production entry point that legitimately wants
    the real store keeps getting it.
    """
    monkeypatch.setenv(_MEMORY_DIR_ENV, str(tmp_path / ".claude" / "pact-memory"))


@pytest.fixture(autouse=True)
def _scrub_session_id_from_test_env(monkeypatch):
    """Remove the developer's live session id from the test environment.

    NOT redundant with the ``PYTEST_CURRENT_TEST`` refusal in
    ``pact_session._discover_session_id``. The two fail on DIFFERENT signals:
    the refusal fails if a spawn-environment allowlist keeps
    ``CLAUDE_CODE_SESSION_ID`` and drops ``PYTEST_CURRENT_TEST``; this scrub
    still holds there, because it removes the id itself rather than depending
    on the pytest signal. Removing either one leaves that case uncovered.

    Both fail open if their signal goes missing, so this is a dependency
    upgrade rather than a safety guarantee.
    """
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


@pytest.fixture(autouse=True)
def _reset_memory_init_state():
    """Clear the process-global lazy-init flag before EVERY test.

    `memory_init._initialized` is a MODULE-LEVEL global, and
    `ensure_memory_ready()` returns early on it BEFORE resolving any path. Two
    of the three things it guards are PER-DATABASE. That was coherent while the
    store path was constant per process; it stopped being coherent once the
    store became per-test, so a test that runs after one which initialised a
    DIFFERENT store is told the work is already done for a store that has never
    been touched. Measured, not inferred: with the flag left set, a second test
    under its own `tmp_path` store received none of the work.

    WHAT THIS DOES NOT CLOSE, stated here so the reset is not read as more than
    it is. `maybe_embed_pending()` has a SECOND, INDEPENDENT guard: a marker
    file at `/tmp/pact_embedding_attempted_<session>`, which is session-scoped
    and shared, not per-database, and which this fixture deliberately does not
    remove -- the marker belongs to any real session on the same machine, and
    `memory_init` separates `clear_embedding_marker()` from
    `reset_initialization()` for exactly that reason. So a second store still
    gets "Already attempted this session" for the catch-up. This fixture
    restores the migration step, not the catch-up.

    NOT the API singleton. `memory_api._instance` is also process-global and
    also unreset, but it does NOT bind a store: it holds `_db_path = None` and
    every operation resolves through `get_db_path()` at call time. Resetting it
    would change no store-related outcome, so it is left alone.

    Imported inside the fixture on purpose. A module-level import would make
    the whole suite's collection depend on `scripts` importing cleanly, turning
    a missing optional dependency into a total collection failure -- the same
    reason `_MEMORY_DIR_ENV` is duplicated as a literal above.
    """
    import sys

    scripts_parent = Path(__file__).parent.parent / "skills" / "pact-memory"
    if str(scripts_parent) not in sys.path:
        sys.path.insert(0, str(scripts_parent))
    try:
        from scripts.memory_init import reset_initialization
    except Exception:
        # The suite must not fail to COLLECT because an optional dependency of
        # the memory package is absent. A test that needs the reset will fail
        # on its own assertion, which is a better signal than a collection error.
        return
    reset_initialization()


# The memory CLI. Reached by subprocess, never imported, for the same reason
# `_MEMORY_DIR_ENV` is a literal above: a module-level import of the memory
# package turns a missing optional dependency into a total collection failure.
_MEMORY_CLI = (
    Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts" / "cli.py"
)


@pytest.fixture
def memory_store(tmp_path):
    """Bring a memory store into existence at a caller path, and return it.

    WHY THIS FIXTURE IS NECESSARY, AND IT IS A CONTRACT AND NOT A CONVENIENCE.
    `cli.main` refuses a `--db-path` that names a store that is ABSENT, for each
    command other than `setup`. A path a caller TYPED is a spelling somebody
    chose, so an absent store there is a typo. `setup` is the one command
    allowed to create at such a path, because bringing a store into existence at
    a named location is its operation.

    So a test that scopes itself with `--db-path` must ASK for its store first.
    Use this fixture rather than a bare `tmp_path / "x.db"`, which names a store
    that does not yet exist and which each command other than `setup` refuses.

    IT SPAWNS `setup` RATHER THAN CALLING `initialize_database`. Two reasons.
    The subprocess binds no store scope in the test process, which the store
    isolation depends on. And it drives the exemption through the real boundary,
    so this fixture fails if the exemption breaks.

    Usage:
        def test_x(self, memory_store):
            db = memory_store("m.db")      # a Path, and the store is present
    """
    def _make(name="memory.db"):
        path = tmp_path / name
        if path.exists():
            return path
        proc = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "setup", "--db-path", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, (
            f"the memory store could not be created at {path}, so the test "
            f"below would measure this failure rather than its own subject. "
            f"rc={proc.returncode} stderr={proc.stderr[:400]!r}"
        )
        assert path.exists(), (
            f"`setup` reported success and left no store at {path}"
        )
        return path

    return _make
