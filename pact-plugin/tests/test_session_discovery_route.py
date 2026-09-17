"""
Location: pact-plugin/tests/test_session_discovery_route.py

Summary: Covers the session-id discovery route in `pact_session.py` — the env
read, the glob, the fail-closed count check, the JSON parse, the type validation
and the per-process cache. Exercises the SHIPPED code, including the shipped
pytest refusal, with no production seam.

Used by/with:
- skills/pact-memory/scripts/pact_session.py: the route under test.
- tests/conftest.py: `_isolate_config_root_to_tmp` redirects `Path.home()`, which
  is what makes running with the refusal disabled safe.

HOW THIS REACHES CODE THAT NORMALLY REFUSES TO RUN UNDER PYTEST. The refusal
reads `os.environ`, so a test can delete `PYTEST_CURRENT_TEST` for its own
duration. That supplies the REAL predicate a different input rather than
replacing it — no stub, no seam, and no production line changes. A seam would
ship a supported way to disable the guard, and the population 6b protects is
exactly the future test author who would find and use it.

WHY THIS IS SAFE. The discovery root is `Path.home() / ".claude" / "pact-sessions"`,
and the autouse fixture redirects `Path.home()` to a per-test tmp. So with the
refusal off, the glob still searches a sandbox.

EVERY TEST HERE ASSERTS ITS SANDBOX FIRST. A test that turns off a safety
control must prove the control it is relying on instead is actually in place. If
a future refactor makes the home-redirect fixture opt-out for this file, these
tests must fail loudly rather than quietly begin globbing the developer's real
session directory with the guard disabled.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from scripts import pact_session


REAL_HOME = Path(os.path.expanduser("~")).resolve()
SYNTHETIC_ID = "synthetic-session-0001"


def _assert_sandboxed():
    """Hard precondition: never run the discovery route against the real home."""
    home = Path.home().resolve()
    assert home != REAL_HOME, (
        "REFUSING to exercise the discovery route: Path.home() is the real home, "
        "so the autouse redirect is not in effect. With the pytest refusal "
        "disabled this would glob the developer's live session directory."
    )


def enable_discovery(monkeypatch):
    """Turn the shipped refusal off, and clear the process cache.

    MUST BE CALLED FROM THE TEST BODY, NOT A FIXTURE. pytest re-sets
    `PYTEST_CURRENT_TEST` at the start of each phase, so a delenv performed
    during setup is undone before the call phase begins — the refusal fires
    anyway and every negative assertion here passes for the wrong reason. That
    is not hypothetical: the first version of this file did exactly that, and
    nine tests went green while the route under test never ran.
    """
    _assert_sandboxed()
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(pact_session, "_discovered_session_id", pact_session._DISCOVERY_UNSET)
    # Non-vacuity guard: prove the refusal's input is actually gone at call time.
    assert not os.environ.get("PYTEST_CURRENT_TEST"), (
        "the pytest marker is still set, so the refusal will fire and every "
        "assertion below would pass without exercising the discovery route"
    )


def _write_context(session_id: str, slug: str = "some-project", body=None) -> Path:
    root = Path.home() / ".claude" / "pact-sessions" / slug / session_id
    root.mkdir(parents=True, exist_ok=True)
    target = root / "pact-session-context.json"
    if body is None:
        body = json.dumps({"session_id": session_id, "project_dir": f"/x/{slug}"})
    target.write_text(body, encoding="utf-8")
    return target


class TestRefusalUnderPytest:
    """The guard itself, exercised as shipped."""

    def test_refuses_while_pytest_marker_is_set(self, monkeypatch):
        _assert_sandboxed()
        monkeypatch.setattr(pact_session, "_discovered_session_id", pact_session._DISCOVERY_UNSET)
        # Deliberately NOT enable_discovery(): this test asserts the guard FIRES.
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "some_test (call)")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SYNTHETIC_ID)
        _write_context(SYNTHETIC_ID)

        assert pact_session.get_session_id_from_context_file() == "", (
            "a context file existed and the env var was set, so the ONLY reason "
            "to return empty is the refusal — this is the non-vacuity leg"
        )


class TestDiscoveryComposition:
    """The whole route: env read, glob, parse, validate."""

    def test_resolves_a_single_matching_context_file(self, monkeypatch):
        enable_discovery(monkeypatch)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SYNTHETIC_ID)
        _write_context(SYNTHETIC_ID)

        assert pact_session.get_session_id_from_context_file() == SYNTHETIC_ID

    def test_absent_env_var_yields_empty(self, monkeypatch):
        enable_discovery(monkeypatch)
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        _write_context(SYNTHETIC_ID)

        assert pact_session.get_session_id_from_context_file() == ""


class TestFailClosedOnCount:
    """`len(matches) != 1`. The comment in the source calls picking the first a
    coin toss over which project's session this is, so both non-one counts are
    pinned rather than assumed."""

    def test_zero_matches_yields_empty(self, monkeypatch):
        enable_discovery(monkeypatch)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SYNTHETIC_ID)
        # No context file written at all.

        assert pact_session.get_session_id_from_context_file() == ""

    def test_two_matching_slugs_yield_empty_rather_than_the_first(
        self, monkeypatch):
        enable_discovery(monkeypatch)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SYNTHETIC_ID)
        _write_context(SYNTHETIC_ID, slug="project-a")
        _write_context(SYNTHETIC_ID, slug="project-b")

        assert pact_session.get_session_id_from_context_file() == "", (
            "two projects recorded the same session id; returning either one "
            "would be a guess"
        )


class TestMalformedContent:
    def test_malformed_json_yields_empty(self, monkeypatch):
        enable_discovery(monkeypatch)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SYNTHETIC_ID)
        _write_context(SYNTHETIC_ID, body="{not json at all")

        assert pact_session.get_session_id_from_context_file() == ""

    def test_non_dict_payload_yields_empty(self, monkeypatch):
        enable_discovery(monkeypatch)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SYNTHETIC_ID)
        _write_context(SYNTHETIC_ID, body=json.dumps(["not", "a", "mapping"]))

        assert pact_session.get_session_id_from_context_file() == ""

    def test_non_string_session_id_yields_empty(self, monkeypatch):
        enable_discovery(monkeypatch)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SYNTHETIC_ID)
        _write_context(SYNTHETIC_ID, body=json.dumps({"session_id": 12345}))

        assert pact_session.get_session_id_from_context_file() == ""


class TestPerProcessCache:
    def test_second_call_does_not_re_glob(self, monkeypatch):
        """The glob walks one directory per session ever recorded, and the
        resolver runs on every memory operation."""
        enable_discovery(monkeypatch)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SYNTHETIC_ID)
        _write_context(SYNTHETIC_ID)

        calls = []
        real = pact_session._resolve_context_on_disk

        def counting(env_session):
            calls.append(1)
            return real(env_session)

        monkeypatch.setattr(pact_session, "_resolve_context_on_disk", counting)

        first = pact_session.get_session_id_from_context_file()
        second = pact_session.get_session_id_from_context_file()

        assert first == second == SYNTHETIC_ID
        assert len(calls) == 1, "discovery must run once per process, not per call"

    def test_no_filesystem_work_when_the_guard_refuses(self, monkeypatch):
        """No env id means the guard answers before any filesystem work."""
        enable_discovery(monkeypatch)
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

        calls = []
        real = pact_session._resolve_context_on_disk
        monkeypatch.setattr(
            pact_session, "_resolve_context_on_disk",
            lambda e: (calls.append(1), real(e))[1],
        )

        # With no env id the guard returns before the lookup, so the expensive
        # call must not happen at all -- not merely happen once.
        assert pact_session.get_session_id_from_context_file() == ""
        assert pact_session.get_session_id_from_context_file() == ""
        assert len(calls) == 0


class TestExplicitArgumentsBypassDiscovery:
    def test_named_context_file_is_read_directly(self, monkeypatch):
        """The pre-existing path must be unchanged: when the caller supplies both
        identifiers, the named file is read and discovery never runs."""
        _assert_sandboxed()
        monkeypatch.setattr(pact_session, "_discovered_session_id", pact_session._DISCOVERY_UNSET)
        _write_context(SYNTHETIC_ID, slug="named-project")

        def _explode():
            raise AssertionError("discovery must not run when both args are given")

        monkeypatch.setattr(pact_session, "_discover_session_id", _explode)

        result = pact_session.get_session_id_from_context_file(
            session_id=SYNTHETIC_ID, project_dir="/x/named-project"
        )

        assert result == SYNTHETIC_ID


class TestCacheDoesNotOutliveTheGuards:
    """A cached answer must never be returned to a caller the guards refuse.

    The cache used to sit ABOVE _discover_session_id, so once populated it
    short-circuited the PYTEST_CURRENT_TEST and CLAUDE_CODE_SESSION_ID guards
    for the life of the process: a caller kept
    receiving the real session id after PYTEST_CURRENT_TEST was set, and after
    CLAUDE_CODE_SESSION_ID was removed.
    """

    def _prime(self, monkeypatch):
        enable_discovery(monkeypatch)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SYNTHETIC_ID)
        _write_context(SYNTHETIC_ID)
        # POSITIVE ARM: the cache must actually be populated, or the assertions
        # below would pass against a resolver that had simply never worked.
        assert pact_session.get_session_id_from_context_file() == SYNTHETIC_ID
        return SYNTHETIC_ID

    def test_pytest_guard_applies_after_the_cache_is_populated(self, monkeypatch):
        self._prime(monkeypatch)
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "some_test (call)")

        assert pact_session.get_session_id_from_context_file() == "", (
            "a populated cache returned the session id to a caller the pytest "
            "guard should have refused"
        )

    def test_absent_env_id_applies_after_the_cache_is_populated(self, monkeypatch):
        self._prime(monkeypatch)
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

        assert pact_session.get_session_id_from_context_file() == "", (
            "a populated cache returned the session id after the environment "
            "no longer supplied one"
        )

    def test_a_different_env_id_is_not_served_from_the_cache(self, monkeypatch):
        """The cache is keyed on the id it was resolved for."""
        self._prime(monkeypatch)
        other = "synthetic-session-0002"
        _write_context(other, slug="other-project")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", other)

        assert pact_session.get_session_id_from_context_file() == other
