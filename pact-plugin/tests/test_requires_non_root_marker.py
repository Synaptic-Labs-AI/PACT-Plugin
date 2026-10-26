"""Pins the `requires_non_root` marker (tests/fixtures/root_guard.py, wired in
tests/conftest.py).

Two halves. The decision function is pinned directly with a monkeypatched
`os.geteuid`, so every branch runs whatever uid the suite has. The conftest
wiring is pinned by a marked sentinel that asserts it is NOT root: under root
it passes only if the hook skipped it, and as any other user it runs and
passes. Remove the hook or misspell the marker and the sentinel goes red under
root, the environment the marker exists for.
"""

import os

import pytest

from fixtures import root_guard


def test_root_gets_the_skip_reason(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    assert root_guard.non_root_skip_reason() == root_guard.REASON


def test_a_non_root_user_gets_no_skip(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    assert root_guard.non_root_skip_reason() is None


def test_a_platform_without_geteuid_gets_no_skip(monkeypatch):
    monkeypatch.delattr(os, "geteuid", raising=False)
    assert root_guard.non_root_skip_reason() is None


def test_the_reason_names_the_marker_and_the_cause():
    assert root_guard.MARKER in root_guard.REASON
    assert "uid 0" in root_guard.REASON


def test_the_marker_is_registered(pytestconfig):
    registered = [line.split(":", 1)[0] for line in pytestconfig.getini("markers")]
    assert root_guard.MARKER in registered


@pytest.mark.requires_non_root
def test_a_marked_test_never_runs_as_root():
    """The wiring sentinel. Under root this body must never execute."""
    assert not (hasattr(os, "geteuid") and os.geteuid() == 0), (
        "a requires_non_root test ran as root: the conftest hook did not skip it"
    )
