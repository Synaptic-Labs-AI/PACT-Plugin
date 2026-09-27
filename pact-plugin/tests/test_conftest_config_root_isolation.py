"""
Location: pact-plugin/tests/test_conftest_config_root_isolation.py
Summary: Positive regression pin for the autouse conftest fixture
         ``_isolate_config_root_to_tmp`` (PR #1189 / #1186). The fixture scrubs
         ``CLAUDE_CONFIG_DIR`` and redirects ``Path.home()`` -> ``tmp_path`` so
         every PACT state writer resolves under the per-test tmp, NEVER the
         operator's real ``~/.claude``. The real-writer suites
         (test_agent_handoff_marker, test_snapshot_marker_root_fallback, ...)
         prove the closure MECHANISM — but each SELF-PATCHES ``Path.home``, so
         they stay green even if the fixture were disabled: a future edit
         removing or breaking the fixture would reopen #1186 SILENTLY (suite
         green, destructive leak resumed). This file is the delete-the-fix
         counter-test that pins the FIXTURE's own closure.
Used by: pytest.
"""
import os

import pytest

from shared.paths import get_claude_config_dir

# Read at IMPORT time — collection runs before any function-scoped autouse
# fixture, so this is the ambient value the scrub will later remove. Capturing
# it is what lets the session-id cell below tell "the scrub fired" apart from
# "there was nothing to scrub", which absence alone cannot distinguish.
_AMBIENT_SESSION_ID = os.environ.get("CLAUDE_CODE_SESSION_ID")

# Same import-time capture discipline for the CLAUDE_PROJECT_DIR scrub pin
# below: on a machine whose shell exports the var (a Claude Code hook process
# or a developer shell that sourced one) the ambient-removal cell
# discriminates; on CI it skips by the same logic as the session-id cell.
_AMBIENT_PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR")


class TestAutouseConfigRootIsolationPinned:
    """Pin the autouse ``_isolate_config_root_to_tmp`` fixture's closure.

    DELIBERATELY does NOT self-patch ``Path.home`` and does NOT set
    ``CLAUDE_CONFIG_DIR`` in-body: it relies SOLELY on the autouse fixture. That
    is what makes it a delete-the-fix counter-test for the fixture itself,
    rather than a redundant mechanism assertion (the closure mechanism is
    already covered by the real-writer suites, which self-patch).
    """

    def test_resolver_lands_under_tmp_not_real_home(self, tmp_path):
        """The SSOT resolver ``get_claude_config_dir()`` — every PACT state
        writer resolves through it — must land under the autouse fixture's
        ``tmp_path``, NOT the operator's real ``~/.claude``.

        Under the fixture: ``CLAUDE_CONFIG_DIR`` is scrubbed (unset) at setup
        and ``Path.home()`` -> ``tmp_path``, so the SSOT resolves to
        ``tmp_path / ".claude"`` (the HOME fallthrough — precedence-1 is empty).

        COUNTER-TEST (the pinning property this file exists for): if the
        fixture's ``monkeypatch.setattr(Path, "home", ...)`` is removed,
        ``Path.home()`` is the operator's real home -> ``get_claude_config_dir``
        resolves to real ``~/.claude`` -> the ``startswith(tmp_path)``
        assertion FAILS. If the scrub is removed AND an ambient
        ``CLAUDE_CONFIG_DIR`` is exported, precedence-1 resolves to that
        ambient value -> the assertion also FAILS. Verified by local
        fixture-disable (setattr line removed), reverted before staging.
        """
        resolved = get_claude_config_dir()

        # (1) Lands under the autouse fixture's tmp, NOT the real home.
        assert str(resolved).startswith(str(tmp_path)), (
            f"get_claude_config_dir() resolved to {resolved!r}, NOT under the "
            f"autouse fixture's tmp_path {str(tmp_path)!r}. The autouse "
            "_isolate_config_root_to_tmp closure has REGRESSED (Path.home "
            "setattr removed, or CLAUDE_CONFIG_DIR scrub removed with an "
            "ambient value leaking through) — #1186's destructive leak would "
            "resume silently under a green suite."
        )
        # (2) Specifically the .claude leaf (the HOME-fallthrough shape), not
        # some other tmp-rooted path — pins the exact resolution contract.
        assert resolved == tmp_path / ".claude", (
            f"expected tmp_path / '.claude', got {resolved!r}"
        )

    def test_scrub_leaves_claude_config_dir_absent_during_body(self, tmp_path):
        """Companion observability for the scrub half of the closure: after the
        autouse fixture's setup scrub fires, ``CLAUDE_CONFIG_DIR`` is absent
        from ``os.environ`` during the test body, so the HOME fallthrough is
        the LIVE resolution path (precedence-1 is empty).

        Honest scope of this cell: it is NOT a strong standalone counter-test —
        a clean ambient env with no ``CLAUDE_CONFIG_DIR`` makes it pass
        trivially. Paired with the cell above it documents and partially pins
        the scrub posture: a regression that leaves an inherited
        ``CLAUDE_CONFIG_DIR`` set would surface here whenever the suite runs
        under an env that exports it.
        """
        assert "CLAUDE_CONFIG_DIR" not in os.environ, (
            "CLAUDE_CONFIG_DIR is present during the test body — the autouse "
            "fixture's setup scrub did not fire; a contributor env exporting it "
            "would leak through and shadow the HOME fallthrough (precedence-1)."
        )
        assert get_claude_config_dir() == tmp_path / ".claude"

    def test_scrub_removes_an_ambient_session_id(self):
        """Pin the SECOND autouse scrub, ``_scrub_session_id_from_test_env``.

        That fixture removes ``CLAUDE_CODE_SESSION_ID`` so a test process cannot
        resolve the developer's live session and compute paths belonging to it.
        It is deliberately NOT redundant with the ``PYTEST_CURRENT_TEST`` refusal
        in ``pact_session._discover_session_id``: the two fail on different
        signals, and the scrub is the one that still holds if a spawn-environment
        allowlist keeps the session id and drops the pytest marker. Nothing else
        in the suite observes it — removing the fixture body leaves every other
        test green.

        STRONGER THAN THE CELL ABOVE, deliberately. Absence alone cannot tell
        "the scrub fired" from "there was nothing to scrub", so this cell SKIPS
        rather than passes when the ambient value was never present. A skip is
        visible in the pytest header; a trivial pass is not.
        """
        if _AMBIENT_SESSION_ID is None:
            pytest.skip(
                "no ambient CLAUDE_CODE_SESSION_ID was present at collection, so "
                "this cell cannot discriminate a working scrub from an absent "
                "input — it is meaningful only when the suite runs under a "
                "session that exports one"
            )

        assert "CLAUDE_CODE_SESSION_ID" not in os.environ, (
            "CLAUDE_CODE_SESSION_ID survived into the test body although the "
            "ambient environment carried one at collection — the autouse scrub "
            "did not fire, and a test resolving the developer's live session id "
            "can compute and write paths that belong to it."
        )


class TestAutouseProjectDirScrubPinned:
    """Pin the autouse ``_scrub_claude_project_dir_env`` fixture's scrub.

    ``CLAUDE_PROJECT_DIR`` is a live production input, not just test plumbing:
    ``pact_context.init()`` derives the session context path from it and
    ``backlog.project_root()`` anchors project resolution on it (NOT the
    process cwd). An ambient value leaking into the suite flips
    environment-sensitive tests from deterministic to machine-dependent — the
    measured shape was three dev-machine-red / CI-green failures
    (test_backlog's "refusal: no root" exit-code arm; both
    test_bootstrap_prompt_gate no-session tests, where init() re-derives a
    path the test's ``_context_path = None`` patch cannot prevent). This
    class is the delete-the-fix counter-test for the scrub: restore the old
    snapshot/restore-only posture (or remove the setup POP) under an
    exporting shell and the cells below go red.
    """

    def test_scrub_removes_an_ambient_project_dir(self):
        """Same skip-unless-ambient discipline as the session-id cell: absence
        alone cannot tell "the scrub fired" from "there was nothing to
        scrub", so skip (visible in the header) when the ambient was clean.
        """
        if _AMBIENT_PROJECT_DIR is None:
            pytest.skip(
                "no ambient CLAUDE_PROJECT_DIR was present at collection, so "
                "this cell cannot discriminate a working scrub from an absent "
                "input — it is meaningful only when the suite runs under a "
                "shell that exports one"
            )

        assert "CLAUDE_PROJECT_DIR" not in os.environ, (
            "CLAUDE_PROJECT_DIR survived into the test body although the "
            "ambient environment carried one at collection — the autouse scrub "
            "did not fire, and env-keyed resolvers (pact_context.init, "
            "backlog.project_root) are resolving against the developer's live "
            "project instead of the test's inputs."
        )

    def test_init_cannot_derive_a_context_path_from_ambient_env(self):
        """Mechanism pin for the measured failure mode: with the var scrubbed,
        ``pact_context.init()`` must leave ``_context_path`` None for an input
        carrying only a session_id — the exact precondition the
        bootstrap no-session tests rely on (no session dir -> no-op gate).
        """
        import shared.pact_context as ctx

        ctx.reset_for_tests()
        try:
            ctx.init({"session_id": "probe-no-project-dir"})
            assert ctx._context_path is None, (
                f"init() derived a context path ({ctx._context_path!r}) from "
                "an input with no project-dir axis — an ambient "
                "CLAUDE_PROJECT_DIR leaked through the autouse scrub and "
                "re-anchored session resolution onto the live machine state."
            )
        finally:
            ctx.reset_for_tests()


# The four variables tests/conftest.py deletes for every test. Each must come
# back after the test, whatever the test did to it.
_SCRUBBED = (
    "CLAUDE_PROJECT_DIR",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_PLUGIN_ROOT",
    "CLAUDE_ENV_FILE",
)


def _held_outside_every_test(values):
    """Put the four variables in the state `values` names -- a string sets one,
    None removes it -- and check that the same state is back once the class's
    last test has been torn down.

    CLASS-SCOPED ON PURPOSE, through the two fixtures below. A class fixture is
    set up before and torn down after every function-scoped fixture of the tests
    inside it, so its teardown sees the environment the NEXT test, or
    `pytest_unconfigure`, would see. A function-scoped check would run inside
    the window the scrubs restore and could not see what they left behind.
    """
    saved = {name: os.environ.get(name) for name in _SCRUBBED}
    for name, value in values.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    yield
    left = {name: os.environ.get(name) for name in _SCRUBBED}
    for name, value in saved.items():  # restore before asserting
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    assert left == values, (
        f"the test changed a scrubbed variable for the rest of the run: {left}, "
        f"expected {values}. Every later test, and the CLAUDE.md guard's "
        "unconfigure, sees the changed value."
    )


@pytest.fixture(scope="class")
def _exported_outside_every_test():
    """A shell that exports all four variables."""
    yield from _held_outside_every_test(
        {name: f"/ambient/{name.lower()}" for name in _SCRUBBED}
    )


@pytest.fixture(scope="class")
def _absent_outside_every_test():
    """A shell that exports none of the four variables -- CI's case."""
    yield from _held_outside_every_test({name: None for name in _SCRUBBED})


@pytest.mark.usefixtures("_exported_outside_every_test")
class TestAScrubbedVariableSurvivesATestThatSetsIt:
    """A test's own `monkeypatch.setenv` of a scrubbed variable must not delete
    the ambient value for the rest of the run.

    It did. A scrub restored the value in its own teardown, and the test's
    `monkeypatch` -- which had recorded the variable as ABSENT, because the
    scrub had already removed it -- was torn down later and deleted it again.
    A failure here reports as an ERROR at teardown of the test below.
    """

    def test_setting_each_scrubbed_variable(self, monkeypatch):
        for name in _SCRUBBED:
            assert name not in os.environ, f"the scrub did not remove {name}"
        for name in _SCRUBBED:
            monkeypatch.setenv(name, "set-by-the-test")


@pytest.mark.usefixtures("_exported_outside_every_test")
class TestAScrubbedVariableSurvivesATestThatSwapsEnviron:
    """A test that replaces `os.environ` with a plain dict for its body must
    not cost the run the ambient values either.

    The scrubs' teardown runs while that replacement is still in place, so a
    restore written through `os.environ` lands in the dict and is thrown away
    when the real environment comes back.
    """

    def test_swapping_environ_for_a_dict(self, monkeypatch):
        for name in _SCRUBBED:
            assert name not in os.environ, f"the scrub did not remove {name}"
        monkeypatch.setattr(os, "environ", {"PATH": os.environ.get("PATH", "")})


@pytest.mark.usefixtures("_absent_outside_every_test")
class TestADirectlyAssignedScrubbedVariableDoesNotLeak:
    """A variable ABSENT before the test and assigned directly in its body
    (`os.environ[name] = ...`, as the concurrency tests do) must not reach the
    next test.

    `monkeypatch` holds no record of a direct assignment, so only the scrub's
    own teardown pop removes it. Delete that pop and this class ERRORs.
    """

    def test_assigning_each_scrubbed_variable_directly(self):
        for name in _SCRUBBED:
            os.environ[name] = "assigned-directly"


@pytest.mark.usefixtures("_absent_outside_every_test")
class TestADirectAssignmentBeforeAnEnvironSwapDoesNotLeak:
    """The same direct assignment, followed by a swap of `os.environ` for a
    dict, must not leak either.

    THE ORDER IS THE TEST. The assignment lands in the REAL environment and
    the swap is still in place when the scrubs' teardown runs, so a pop that
    reads `os.environ` at teardown empties the dict and leaves the real value
    behind. Only a pop through the environment captured at setup removes it.
    Swapping first would put the assignment in the dict, and nothing could
    leak.
    """

    def test_assigning_directly_then_swapping_environ(self, monkeypatch):
        for name in _SCRUBBED:
            os.environ[name] = "assigned-directly"
        monkeypatch.setattr(os, "environ", {"PATH": os.environ.get("PATH", "")})
