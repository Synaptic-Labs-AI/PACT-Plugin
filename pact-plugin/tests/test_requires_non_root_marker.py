"""Pins the `requires_non_root` marker (tests/fixtures/root_guard.py, wired in
tests/conftest.py).

Three parts. The decision function is pinned directly with a monkeypatched
`os.geteuid`, so every branch runs whatever uid the suite has.

The conftest WIRING is pinned by a child pytest run that pretends to be root,
so the pin holds on every runner, CI included, and not only under uid 0. The
parent starts a child pytest on one marked test of this file, with a flag in
the environment. In the child, this module replaces `os.geteuid` at import,
before any test runs, so the real `pytest_runtest_setup` hook in the real
tests/conftest.py sees uid 0. The parent asserts that the child SKIPPED the
test with the marker's reason. Remove the hook, or break its marker lookup, and
the child runs the test body, which fails, and the parent goes red as any user.
The pattern is the one tests/test_conftest_remote_scrub.py uses: a subprocess
run, so no pytester plugin needs registering and no process-global state of
the parent run is touched.

The marked sentinel at the end asserts it is NOT root. As root it passes only
if the hook skipped it; as any other user it runs and passes. It is the same
check made in the real environment, kept because it needs no child process.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fixtures import root_guard

# Set only in the child run the wiring test starts. In every other run the
# child test is skipped and os.geteuid is left alone.
_CHILD_FLAG = "PACT_NON_ROOT_MARKER_META_CHILD"
_IN_CHILD = os.environ.get(_CHILD_FLAG) == "1"

if _IN_CHILD:
    # The child process only: make the conftest hook see uid 0. This runs at
    # collection, before any setup hook, and the decision function looks
    # os.geteuid up at call time.
    os.geteuid = lambda: 0

_TESTS_DIR = Path(__file__).resolve().parent


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


@pytest.mark.skipif(not _IN_CHILD,
                    reason="runs only inside the wiring test's child pytest")
@pytest.mark.requires_non_root
def test_child_marked_test_under_a_pretend_root():
    """The child's subject. The hook must skip it, so this body never runs."""
    pytest.fail("the conftest hook did not skip a requires_non_root test at uid 0")


@pytest.mark.skipif(not hasattr(os, "geteuid"),
                    reason="no geteuid on this platform, so the marker does nothing")
def test_the_conftest_hook_skips_a_marked_test_at_uid_0():
    """The wiring pin, on any uid. RED if the hook is removed or stops matching
    the marker: the child then runs its test body and reports a failure."""
    env = dict(os.environ)
    env[_CHILD_FLAG] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
         "-p", "no:randomly", "-p", "no:xdist",
         f"{Path(__file__).name}::test_child_marked_test_under_a_pretend_root"],
        cwd=str(_TESTS_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "1 skipped" in output, output
    # The skip came from THIS marker's hook, not from the child-only skipif.
    assert root_guard.REASON in output, output


@pytest.mark.requires_non_root
def test_a_marked_test_never_runs_as_root():
    """The in-place sentinel. Under root this body must never execute."""
    assert not (hasattr(os, "geteuid") and os.geteuid() == 0), (
        "a requires_non_root test ran as root: the conftest hook did not skip it"
    )
