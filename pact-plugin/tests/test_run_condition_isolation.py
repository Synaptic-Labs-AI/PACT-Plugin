"""
Location: pact-plugin/tests/test_run_condition_isolation.py
Summary: Pins the session-wide isolation tests/conftest.py sets up so that one
         run's outcome does not depend on what an earlier run or the machine
         left behind.
Used by: the suite; the settings under test live in tests/conftest.py.
"""
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixtures.session_marker import embedding_marker, fresh_session_id

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFTEST = Path(__file__).resolve().with_name("conftest.py")


def test_hypothesis_keeps_no_example_database():
    hypothesis = pytest.importorskip("hypothesis")
    assert hypothesis.settings.default.database is None


def test_hypothesis_stores_nothing_in_the_tree():
    pytest.importorskip("hypothesis")
    from hypothesis.configuration import storage_directory

    where = Path(storage_directory("constants", intent_to_write=False).path).resolve()
    home = Path(os.environ["HOME"]).resolve()
    assert where.is_relative_to(home), where
    assert not where.is_relative_to(_REPO_ROOT), where


def _merge_guard_caches():
    import shared.merge_guard_common as merge_guard_common

    return merge_guard_common, (merge_guard_common._git_config_aliases,
                                merge_guard_common._git_commands)


def _fill_merge_guard_caches(cwd):
    merge_guard_common, caches = _merge_guard_caches()
    merge_guard_common._git_config_aliases(str(cwd))
    merge_guard_common._git_commands(str(cwd))
    return [cache.cache_info().currsize for cache in caches]


@pytest.fixture(scope="class")
def _merge_guard_caches_filled_around_the_test(tmp_path_factory):
    """Fill both caches before the test's own fixtures run, and check them after.

    Class scope sets this up before the function-scoped autouse clear and tears
    it down after it, so the test sees the clear on both sides of itself without
    depending on any other test running first in the same worker.
    """
    assert all(_fill_merge_guard_caches(tmp_path_factory.mktemp("merge-cache"))), (
        "filling the merge-guard caches left one empty, so this arm proves nothing")
    yield
    _, caches = _merge_guard_caches()
    assert [cache.cache_info().currsize for cache in caches] == [0, 0], (
        "the merge-guard caches the test filled survived its teardown: the "
        "_reset_merge_guard_git_caches clear AFTER the test is missing")


@pytest.mark.usefixtures("_merge_guard_caches_filled_around_the_test")
class TestTheMergeGuardGitCachesAreClearedAroundEveryTest:
    def test_a_test_starts_with_empty_caches_and_its_fills_are_cleared(self, request, tmp_path):
        assert "_reset_merge_guard_git_caches" in request.fixturenames
        _, caches = _merge_guard_caches()
        assert [cache.cache_info().currsize for cache in caches] == [0, 0], (
            "the merge-guard caches filled before this test reached its body: the "
            "_reset_merge_guard_git_caches clear BEFORE the test is missing")
        # Fill both again; the class fixture checks they are cleared afterwards.
        assert all(_fill_merge_guard_caches(tmp_path))


def test_git_ignores_the_machine_system_config(tmp_path, monkeypatch):
    assert os.environ["GIT_CONFIG_NOSYSTEM"] == "1"
    # A system file with a value in it: a child git that inherits this session's
    # environment does not read it, and one without the variable does (the control).
    system = tmp_path / "gitconfig"
    system.write_text("[pact]\n\tprobe = system\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system))

    def probe(env):
        return subprocess.run(["git", "config", "--get", "pact.probe"], cwd=tmp_path, env=env,
                              capture_output=True, text=True, timeout=30).stdout.strip()

    assert probe(None) == ""
    control = {k: v for k, v in os.environ.items() if k != "GIT_CONFIG_NOSYSTEM"}
    assert probe(control) == "system"


def test_fresh_session_id_refuses_an_id_it_already_handed_out(monkeypatch):
    holder = SimpleNamespace(SID=None)
    seen: set[str] = set()
    fixed = f"refusal-arm-{uuid.uuid4().hex}"
    with fresh_session_id(monkeypatch, holder, "SID", lambda: fixed, seen) as session_id:
        assert holder.SID == session_id == fixed
    with pytest.raises(AssertionError, match="already used by an earlier test"):
        with fresh_session_id(monkeypatch, holder, "SID", lambda: fixed, seen):
            pass


# The real suite conftest, loaded by path, with only the importer-marker guard
# exposed: the rig test file then runs under that fixture and nothing else.
_MARKER_RIG_CONFTEST = '''
import importlib.util

_spec = importlib.util.spec_from_file_location("pact_suite_conftest", {conftest!r})
_suite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_suite)
_refuse_a_marker_under_an_importer_session_id = _suite._refuse_a_marker_under_an_importer_session_id
'''

_MARKER_RIG_TESTS = '''
from pathlib import Path

from fixtures.session_marker import embedding_marker, importer_session_id


def test_leaves_a_marker_under_an_importer_id():
    session_id = importer_session_id()
    Path(__file__).with_name("session_id.txt").write_text(session_id)
    embedding_marker(session_id).touch()


def test_registers_an_importer_id_and_leaves_no_marker():
    importer_session_id()
'''


def test_the_conftest_fails_a_test_that_leaves_a_marker_under_an_importer_id(tmp_path):
    (tmp_path / "conftest.py").write_text(
        _MARKER_RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    (tmp_path / "test_marker_rig.py").write_text(_MARKER_RIG_TESTS, encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("PYTEST_XDIST_")
           and key not in ("PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST")}
    record = tmp_path / "session_id.txt"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider",
             "-p", "no:randomly", "test_marker_rig.py"],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
        output = proc.stdout + proc.stderr
        assert record.exists(), output
        marker = embedding_marker(record.read_text())
        # A teardown error is counted beside its passed call, so the marker test
        # shows as one of the two passes and as the one error.
        assert re.search(r"^2 passed, 1 error in ", output, re.MULTILINE), output
        assert proc.returncode == 1, output
        assert ("ERROR test_marker_rig.py::test_leaves_a_marker_under_an_importer_id"
                in output), output
        assert "fixtures.session_marker.fresh_session_id" in output, output
        assert not marker.exists(), "the guard failed the test but left its marker behind"
    finally:
        if record.exists():
            embedding_marker(record.read_text()).unlink(missing_ok=True)
