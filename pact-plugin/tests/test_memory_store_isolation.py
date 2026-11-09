"""The memory store must never resolve to the operator's real store under test.

WHAT THESE TESTS ARE FOR, because a passing suite is not evidence here. The
suite passed with the leak wide open: 13,812 tests green while 60 of them
opened write-capable connections to the live store. So "the tests pass" cannot
distinguish an isolated system from a leaking one, and every assertion below is
written to FAIL on the pre-fix code rather than to describe the post-fix code.

THE TWO MECHANISMS THAT LEAKED, each with a test here:
  1. IMPORT-TIME BINDING. `config` computed its paths from `Path.home()` when
     the module first imported, so a later redirect reached a value that no
     longer existed to change. Protection depended on import ORDER.
  2. THE PROCESS BOUNDARY. An in-process patch does not survive `exec`, so a
     spawned CLI child resolved the real home however carefully the parent had
     been redirected.

WHAT MUST KEEP WORKING, and it outranks the isolation. Production entry points
that pass no path SHOULD reach the real store: that is what `archive_pin
--index N` does, and `/PACT:prune-memory` keys its refuse-or-proceed decision
on it. A fix that isolates tests by making the default unreachable would refuse
an honest user command, which damages real work rather than merely risking it.
`TestProductionStillResolvesTheRealStore` is that guarantee.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_PARENT = Path(__file__).parent.parent / "skills" / "pact-memory"

from scripts.config import (  # noqa: E402
    MEMORY_DIR_ENV,
    compute_db_path,
    get_memory_dir,
)

REAL_STORE_SUFFIX = os.path.join(".claude", "pact-memory")

# THE OPERATOR'S TRUE HOME, captured at MODULE IMPORT.
#
# Deliberately bound early, which is the opposite of what the fix does, and for
# a reason that does not apply there: this must be the home as it was BEFORE any
# fixture ran. The sibling autouse fixture `_isolate_config_root_to_tmp` patches
# `Path.home()` to a tmp tree for every test, so calling `Path.home()` inside a
# test yields the REDIRECTED home and an assertion built on it compares a tmp
# path against itself. Module import happens at collection, before any fixture,
# so this is the last honest reading available. `os.path.expanduser` is used
# because it is not the attribute the fixture replaces.
_REAL_HOME = Path(os.path.expanduser("~"))

# THE STORE OVERRIDE AS IT STOOD AT COLLECTION, captured at MODULE IMPORT.
#
# Bound early for the same reason as `_REAL_HOME` above, against a different
# axis. Two isolation layers set this variable: `pytest_configure` assigns a
# session-wide store BEFORE collection, and the autouse fixture
# `_isolate_memory_store_to_tmp` then overrides it per test. The fixture runs
# after collection, so BY THE TIME ANY TEST BODY EXECUTES THE SESSION VALUE IS
# GONE -- overwritten, not merely shadowed.
#
# That is why removing the session layer changes NOTHING a test body can see:
# the in-test state is identical either way, and no assertion over identical
# state can distinguish them. Module import is the only moment inside the
# session at which the session layer's own value is still readable, so this
# capture is what makes `TestEachIsolationLayerHasAWitness` possible at all.
_STORE_AT_COLLECTION = os.environ.get(MEMORY_DIR_ENV)


def _session_floor_at_collection():
    """The session store installed by `conftest.pytest_configure`, read now.

    RESOLVED AT COLLECTION FOR TWO SEPARATE REASONS, both measured.

    1. NOT VIA `import conftest`. That statement builds a SECOND module object
       under its own `sys.modules` key and RE-EXECUTES the module body, which
       calls `tempfile.mkdtemp` again. The value it reports is a directory no
       test ever used, so an equality check against it can never pass -- and it
       would fail for a reason that has nothing to do with the layer under test.
       It also leaks an empty directory on every call.
    2. AT COLLECTION, FOR TWO STRUCTURAL REASONS. The per-test fixture overwrites
       this variable before any test body runs, so collection is the last moment
       the session layer's value is readable at all (see `_STORE_AT_COLLECTION`).
       And this helper cannot use `_loaded_conftest` below even if it wanted to:
       `request` is a fixture, so it does not exist at module import, which is
       why the path scan survives beside the deterministic lookup.

       An earlier version of this note justified collection time by the absence
       of a duplicate module, which a re-import in
       `TestTheConftestLiteralMatchesItsSource` used to create. That re-import is
       gone and the justification went with it: collection excludes a duplicate
       made by a test BODY, but not one made at another module's IMPORT time by a
       module collected earlier. No module does that today, which is contingent
       rather than structural. The `len(found) != 1` check below is the detector
       either way.

    Match on the resolved file path rather than on a module name, so this stays
    correct however pytest keys the module.
    """
    target = str((Path(__file__).parent / "conftest.py").resolve())
    found = [
        m for m in list(sys.modules.values())
        if getattr(m, "__file__", None)
        and str(Path(m.__file__).resolve()) == target
    ]
    if len(found) != 1:
        return None
    return getattr(found[0], "_SESSION_MEMORY_DIR", None)


_SESSION_FLOOR_AT_COLLECTION = _session_floor_at_collection()


def _run_child(code: str, env: dict) -> str:
    """Run `code` in a FRESH interpreter and return its stdout, stripped.

    A fresh process is mandatory rather than tidy. The binding under repair is
    evaluated at import, so an in-process check performed after redirecting
    confirms only that the redirect took effect; it cannot observe the defect,
    which is that the value was already fixed before the redirect happened.
    """
    child_env = dict(os.environ)
    child_env.update(env)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=child_env, timeout=60,
    )
    assert result.returncode == 0, f"child failed: {result.stderr}"
    return result.stdout.strip()


_RESOLVE_IN_CHILD = (
    "import sys; sys.path.insert(0, %r)\n"
    "from scripts.config import compute_db_path\n"
    "print(compute_db_path())\n" % str(SCRIPTS_PARENT)
)


def _loaded_conftest(request):
    """Return the conftest module pytest LOADED, never a fresh import.

    A bare `import conftest` does NOT return that module. It builds a SECOND
    object from the same file and re-executes the body, leaving two entries in
    `sys.modules` for one `__file__`. That duplication has already cost twice:
    it contaminated another test's module lookup order-dependently, and it made
    a live detector look dead under mutation, because patching one object left
    the other untouched.

    THE LOOKUP FAILS LOUDLY ON PURPOSE. Every caller compares against the module
    this returns, so a silent `None` would turn those assertions vacuous while
    every arm stayed green -- strictly worse than the fresh import it replaces.
    """
    path = Path(__file__).parent / "conftest.py"
    plugin = request.config.pluginmanager.get_plugin(str(path))
    assert plugin is not None, (
        f"pytest has no conftest plugin registered for {path}, so this helper "
        "cannot reach the harness. Failing here is deliberate: the assertions "
        "that call it would otherwise pass while comparing nothing."
    )
    return plugin


class TestTheConftestLiteralMatchesItsSource:
    """conftest duplicates the variable name; a rename must not un-isolate."""

    def test_conftest_env_name_equals_config_env_name(self, request):
        """The duplicated literal is pinned to its source.

        `conftest.py` cannot import `scripts.config` at load time without making
        the whole suite depend on that package importing cleanly, so it carries
        its own copy of the name. This test is what makes that copy safe: rename
        `MEMORY_DIR_ENV` without updating conftest and this fails loudly, rather
        than every test silently resolving the operator's real store again.

        IT INSPECTS THE LOADED MODULE, AND THE MESSAGE BELOW IS WHY. That message
        asserts a property of THIS RUN -- that every test is resolving the real
        store. Only the object whose `pytest_configure` set the variable, and
        whose autouse fixture sets it per test, can support that claim. A fresh
        `import conftest` proves a fact about the FILE instead, from a copy with
        no causal role in the isolation it is reporting on.
        """
        conftest = _loaded_conftest(request)

        assert conftest._MEMORY_DIR_ENV == MEMORY_DIR_ENV, (
            f"conftest isolates on {conftest._MEMORY_DIR_ENV!r} but the resolver "
            f"honours {MEMORY_DIR_ENV!r}. Every test is running against the real "
            f"store. Update conftest._MEMORY_DIR_ENV to match."
        )


class TestTheSessionDefaultIsUnconditional:
    """The collection-time assignment must not honour an inherited value.

    ADDED BECAUSE A MUTATION FOUND THE GAP, not because it was designed in.
    Changing `pytest_configure`'s assignment to `setdefault` and pre-setting
    `PACT_TEST_MEMORY_DIR` to the real store left every other test in this file
    GREEN, because the per-test autouse fixture overrides the variable anyway.
    The exposure that survives is narrower and still real: anything resolving
    OUTSIDE a fixture's reach — collection-time imports, session-scoped setup —
    would read the operator's own value and point at the store this exists to
    protect. A contributor who has relocated their store exports that variable.

    STRUCTURAL, AND SAID SO PLAINLY. This inspects source text rather than
    behaviour, because the assignment happens before collection and no test can
    observe the pre-collection state from inside the session. It is a guard
    against reintroducing a known fail-open, not a proof of the runtime property.
    """

    def test_pytest_configure_assigns_rather_than_setdefaults(self, request):
        import inspect

        # THIS ASSERTION IS INDIFFERENT TO WHICH OBJECT IT GETS, because
        # `inspect.getsource` resolves through `__file__` and both objects share
        # one. The helper is used anyway: the duplication comes from the IMPORT
        # STATEMENT, not from the assertion, so leaving a bare import here would
        # keep building the second module and removing it from the other test
        # would remove none of the duplication.
        conftest = _loaded_conftest(request)

        source = inspect.getsource(conftest.pytest_configure)
        assert "os.environ[_MEMORY_DIR_ENV] = " in source, (
            "the session-wide memory-store redirect is no longer an "
            "unconditional assignment"
        )
        assert "setdefault(_MEMORY_DIR_ENV" not in source, (
            "`setdefault` honours an inherited PACT_TEST_MEMORY_DIR, so a contributor "
            "who has relocated their own store would have collection-time "
            "resolution point at it. The fail direction is ALLOW and it is silent."
        )


class TestResolutionIsLateNotImportTime:
    """Mechanism 1: the value must not be fixed at import."""

    def test_env_change_after_import_still_moves_the_store(self, tmp_path):
        """FAILS PRE-FIX. `config.DB_PATH` was a constant, so a change made
        after import moved nothing."""
        first = compute_db_path()
        moved = tmp_path / "relocated"
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(MEMORY_DIR_ENV, str(moved))
            second = compute_db_path()
        assert second == moved / "memory.db"
        assert second != first

    def test_home_change_after_import_still_moves_the_store(self, tmp_path):
        """FAILS PRE-FIX, and this is the ORDER-DEPENDENCE test.

        With no override set, the resolver must consult `Path.home()` at CALL
        time. Pre-fix it consulted a value captured at import, so this returned
        the real home no matter what the patch said.
        """
        elsewhere = tmp_path / "elsewhere"
        with pytest.MonkeyPatch.context() as mp:
            mp.delenv(MEMORY_DIR_ENV, raising=False)
            mp.setattr(Path, "home", lambda: elsewhere)
            assert get_memory_dir() == elsewhere / ".claude" / "pact-memory"

    def test_empty_override_is_treated_as_unset(self, tmp_path):
        """An empty value must not relocate the store to a relative path."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(MEMORY_DIR_ENV, "")
            mp.setattr(Path, "home", lambda: tmp_path)
            assert get_memory_dir() == tmp_path / ".claude" / "pact-memory"


class TestRedirectCrossesTheProcessBoundary:
    """Mechanism 2: a child interpreter must inherit the redirect."""

    def test_child_process_honours_the_override(self, tmp_path):
        """FAILS PRE-FIX for every spawned CLI test, which was the larger half
        of the exposure: no in-process patch survives `exec`."""
        target = tmp_path / "child-store"
        out = _run_child(_RESOLVE_IN_CHILD, {MEMORY_DIR_ENV: str(target)})
        assert out == str(target / "memory.db")

    def test_child_process_does_not_reach_the_real_store(self, tmp_path):
        """The property stated as the thing we actually care about."""
        target = tmp_path / "child-store"
        out = _run_child(_RESOLVE_IN_CHILD, {MEMORY_DIR_ENV: str(target)})
        assert out == str(target / "memory.db")
        assert not out.startswith(str(_REAL_HOME / ".claude" / "pact-memory"))


class TestProductionStillResolvesTheRealStore:
    """CARDINAL. Over-block is worse than the leak it would prevent.

    The leak has damaged nothing to date. A refused good-faith command damages
    the user's work immediately, so these tests rank above the isolation ones.
    """

    def test_no_override_resolves_under_the_real_home(self):
        """A production process sets nothing, and must reach the real store."""
        out = _run_child(
            "import sys; sys.path.insert(0, %r)\n"
            "import os\n"
            "os.environ.pop('PACT_TEST_MEMORY_DIR', None)\n"
            "from scripts.config import compute_db_path\n"
            "print(compute_db_path())\n" % str(SCRIPTS_PARENT),
            {},
        )
        expected = _REAL_HOME / ".claude" / "pact-memory" / "memory.db"
        assert out == str(expected), (
            "a production caller that sets no override must still resolve the "
            "real store; refusing it would break `archive_pin --index N`, which "
            "passes no --db-path on purpose"
        )

    def test_resolution_creates_nothing_by_itself(self, tmp_path):
        """Resolving a path must not have side effects.

        `compute_db_path` in `config` only computes. Directory creation belongs to
        the callers that intend it, so merely asking where the store is can
        never create a stray tree in someone's home.
        """
        target = tmp_path / "never-created"
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(MEMORY_DIR_ENV, str(target))
            compute_db_path()
        assert not target.exists()


class TestTheSuiteIsActuallyIsolatedRightNow:
    """The end-to-end property, asserted from inside the running suite."""

    def test_this_very_test_resolves_away_from_the_real_store(self):
        """If the autouse fixture ever stops working, this fails immediately."""
        resolved = str(compute_db_path())
        real = str(_REAL_HOME / ".claude" / "pact-memory")
        assert not resolved.startswith(real), (
            f"this test resolves the memory store to {resolved}, which is the "
            f"operator's real store. The isolation fixture is not working."
        )

    def test_the_override_is_set_for_every_test(self):
        assert os.environ.get(MEMORY_DIR_ENV), (
            "no memory-store override is set, so resolution falls back to the "
            "real home for every test in this run"
        )


def _dirty_process_flag(monkeypatch):
    """Set the lazy-init flag without doing environment-dependent real work."""
    from scripts import memory_init

    for name in (
        "check_and_install_dependencies",
        "maybe_migrate_embeddings",
        "maybe_embed_pending",
    ):
        monkeypatch.setattr(
            memory_init, name,
            lambda *a, **k: {"status": "ok", "installed": [], "failed": []},
        )
    memory_init.ensure_memory_ready()
    assert memory_init.is_initialized(), (
        "the test could not establish its dirty-flag precondition"
    )
    return memory_init


class TestInitStateDoesNotLeakBetweenStores:
    """Each test owns its dirty precondition; no sibling or worker supplies it."""

    def test_a_leaves_the_process_flag_set_for_its_own_store(self, monkeypatch):
        """The setup used by both tests sets the process-global flag."""
        memory_init = _dirty_process_flag(monkeypatch)
        assert memory_init.is_initialized()

    def test_b_starts_from_a_clean_flag(self, monkeypatch):
        """The owner reset clears a flag this test dirtied itself."""
        memory_init = _dirty_process_flag(monkeypatch)
        memory_init.reset_initialization()
        assert not memory_init.is_initialized(), (
            "reset_initialization() left the lazy-init flag set after this test "
            "established it. ensure_memory_ready() would return early for this "
            "test's store and skip the per-database work it guards."
        )


class TestAutouseResetIntegration:
    """The autouse reset is a separate integration obligation from B's unit assertion."""

    def test_a_observer_establishes_dirty_flag(self, monkeypatch):
        _dirty_process_flag(monkeypatch)

    def test_b_observer_relies_on_autouse_reset(self):
        from scripts import memory_init

        assert not memory_init.is_initialized(), (
            "the autouse reset did not clear the dirty flag from the previous test. "
            "This observer deliberately has no owner-level reset of its own."
        )

    def test_removing_autouse_reset_reddens_the_observer(self, tmp_path):
        """Enabled pair passes; only the reset's removal makes the observer fail."""
        child_test = tmp_path / "test_autouse_reset_observer.py"
        child_test.write_text(
            "import os\n"
            "import sys\n"
            "sys.path.insert(0, os.environ['PACT_AUTOUSE_SOURCE_ROOT'] + '/skills/pact-memory')\n"
            "from scripts import memory_init\n\n"
            "def test_a_observer_establishes_dirty_flag(monkeypatch):\n"
            "    for name in ('check_and_install_dependencies', 'maybe_migrate_embeddings', 'maybe_embed_pending'):\n"
            "        monkeypatch.setattr(memory_init, name, lambda *a, **k: {'status': 'ok', 'installed': [], 'failed': []})\n"
            "    memory_init.ensure_memory_ready()\n"
            "    assert memory_init.is_initialized()\n\n"
            "def test_b_observer_relies_on_autouse_reset():\n"
            "    assert not memory_init.is_initialized(), (\n"
            "        'the autouse reset did not clear the dirty flag from the previous test. '\n"
            "        'This observer deliberately has no owner-level reset of its own.'\n"
            "    )\n",
            encoding="utf-8",
        )
        enabled_conftest = (
            "import pytest\n\n"
            "@pytest.fixture(autouse=True)\n"
            "def _reset_memory_init_state():\n"
            "    from scripts.memory_init import reset_initialization\n"
            "    reset_initialization()\n"
        )
        (tmp_path / "conftest.py").write_text(enabled_conftest, encoding="utf-8")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env["PACT_AUTOUSE_SOURCE_ROOT"] = str(Path(__file__).parent.parent)
        for name in ("PYTEST_ADDOPTS", "GITHUB_ACTIONS", "GITHUB_WORKSPACE"):
            env.pop(name, None)
        for name in list(env):
            if name.startswith("PYTEST_XDIST_"):
                env.pop(name, None)

        def run_pair():
            return subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 "-p", "no:randomly", "-p", "no:xdist",
                 "test_autouse_reset_observer.py"],
                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
            )

        enabled = run_pair()
        assert enabled.returncode == 0, enabled.stdout + enabled.stderr
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n"
            "@pytest.fixture(autouse=True)\n"
            "def _reset_memory_init_state():\n"
            "    pass\n",
            encoding="utf-8",
        )
        disabled = run_pair()
        assert disabled.returncode == 1, disabled.stdout + disabled.stderr
        assert "test_b_observer_relies_on_autouse_reset" in disabled.stdout


class TestEachIsolationLayerHasAWitness:
    """One test per isolation layer, because ONE TEST CANNOT COVER BOTH.

    The store is isolated twice over: `pytest_configure` assigns a session-wide
    store before collection (the FLOOR), and an autouse fixture overrides it per
    test (the PER-TEST redirect). The two are genuinely redundant for real-store
    protection, so REMOVING EITHER ONE LEAVES THE SUITE GREEN -- each layer
    covers for the other's absence. Both mutations were run and every other test
    in this file survived both. That is the gap these two close.

    WHY NOT ONE TEST. The per-test fixture OVERWRITES the session value, and it
    runs after collection. So with the floor removed, the state visible to a test
    body is IDENTICAL to the unmutated state, and no predicate over identical
    state can differ. This is a structural limit, not a missing assertion: the
    floor must be witnessed from collection time (see `_STORE_AT_COLLECTION`),
    and the per-test redirect from inside a test body. Two vantages, two tests.

    These are BEHAVIOURAL. They read the values the run actually used. The
    sibling `TestTheSessionDefaultIsUnconditional` inspects source text instead,
    and remains necessary: it catches a `setdefault` that is present and
    fail-open, where these catch a layer that is absent or ineffective for ANY
    reason. Neither subsumes the other.
    """

    def test_the_per_test_redirect_is_in_force_for_this_very_test(self, tmp_path):
        """FAILS when the autouse per-test fixture is removed.

        With that fixture gone the resolver returns the session floor, which is a
        session-wide temp directory and NOT under this test's `tmp_path`. Nothing
        else in this file notices that change.
        """
        resolved = get_memory_dir()
        assert resolved.is_relative_to(tmp_path), (
            f"the memory store resolves to {resolved}, which is NOT under this "
            f"test's own tmp_path ({tmp_path}). The per-test isolation fixture "
            f"is not in force, so tests share one store and can see each "
            f"other's writes."
        )

    def test_the_session_floor_was_applied_before_collection(self):
        """FAILS when the `pytest_configure` assignment is removed.

        Asserts the override in force AT COLLECTION was the one the floor
        installs. Equality against the floor's own value is deliberate: a weaker
        'is set and is not the real store' check stays GREEN for a contributor
        who exports `PACT_TEST_MEMORY_DIR` to some harmless directory of their own,
        which is precisely the inherited-value fail-open the floor exists to
        prevent.
        """
        expected = _SESSION_FLOOR_AT_COLLECTION
        assert expected is not None, (
            "could not identify the loaded conftest at collection, so this "
            "test has no reference value to compare against. It would pass or "
            "fail for reasons unrelated to the isolation layer -- fix the "
            "lookup rather than the assertion it feeds."
        )
        assert _STORE_AT_COLLECTION == expected, (
            f"at collection the memory store resolved to "
            f"{_STORE_AT_COLLECTION!r}, but the session floor installs "
            f"{expected!r}. Anything resolving outside a fixture's reach -- a "
            f"collection-time import, session-scoped setup -- used the wrong "
            f"store. If the value is None the floor never ran; if it is some "
            f"other path it was inherited from the environment."
        )
