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
from helpers import GIT_REDIRECTING_VARIABLES, is_git_redirecting_variable

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


def test_the_variable_set_covers_gits_own_repository_local_list():
    listed = subprocess.run(["git", "rev-parse", "--local-env-vars"], cwd=_REPO_ROOT,
                            capture_output=True, text=True, timeout=30, check=True).stdout.split()
    assert "GIT_DIR" in listed, listed
    missing = sorted(set(listed) - set(GIT_REDIRECTING_VARIABLES))
    assert not missing, ("this git clears these when it enters another repository; add them "
                         "to helpers.GIT_REDIRECTING_VARIABLES: %s" % missing)


# The real suite conftest, loaded by path, so its module-level environment setup
# runs in the child session and nothing else of it is needed.
_GIT_RIG_CONFTEST = '''
import importlib.util

_spec = importlib.util.spec_from_file_location("pact_suite_conftest", {conftest!r})
_spec.loader.exec_module(importlib.util.module_from_spec(_spec))
'''

_GIT_RIG_TESTS = '''
import os
import subprocess

from helpers import is_git_redirecting_variable


def _git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=rig", "-c", "user.email=rig@invalid", *args],
                          cwd=cwd, check=True, capture_output=True, text=True, timeout=60).stdout


def test_a_rig_commits_into_its_own_repository(tmp_path):
    (tmp_path / "rig.txt").write_text("rig\\n", encoding="utf-8")
    _git("init", "-q", cwd=tmp_path)
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", "rig", cwd=tmp_path)
    assert (tmp_path / ".git" / "HEAD").is_file()
    assert _git("rev-list", "--count", "HEAD", cwd=tmp_path).strip() == "1"
    assert [name for name in os.environ if is_git_redirecting_variable(name)] == []
'''


def _shell_exports(decoy):
    """What a shell can export that points git at `decoy`, by row."""
    hook = {"GIT_DIR": str(decoy / ".git"), "GIT_INDEX_FILE": str(decoy / ".git" / "index")}
    every = {name: "1" for name in GIT_REDIRECTING_VARIABLES}
    every.update({
        "GIT_DIR": str(decoy / ".git"), "GIT_WORK_TREE": str(decoy),
        "GIT_INDEX_FILE": str(decoy / ".git" / "index"), "GIT_COMMON_DIR": str(decoy / ".git"),
        "GIT_OBJECT_DIRECTORY": str(decoy / ".git" / "objects"),
        "GIT_CONFIG_GLOBAL": str(decoy / "gitconfig"), "GIT_CONFIG_SYSTEM": str(decoy / "gitconfig"),
        "GIT_CONFIG": str(decoy / "gitconfig"), "GIT_CEILING_DIRECTORIES": str(decoy),
        "GIT_CONFIG_PARAMETERS": "'core.worktree'='%s'" % decoy,
        "GIT_CONFIG_KEY_0": "core.worktree", "GIT_CONFIG_VALUE_0": str(decoy),
        "GIT_PREFIX": "", "GIT_NAMESPACE": "decoy",
    })
    return {"hook": hook, "work-tree": {**hook, "GIT_WORK_TREE": str(decoy)}, "every": every}


@pytest.mark.parametrize("row", ["hook", "work-tree", "every"])
def test_a_rig_ignores_a_repository_the_shell_points_git_at(tmp_path, row):
    """A child session whose shell points git at a decoy repository: the git
    variables a hook exports, those plus GIT_WORK_TREE, and every variable in the
    set. Its rig test runs git init, add and commit with the inherited
    environment. MUTANT: tests/conftest.py keeps the shell's git variables. The
    rig then reinitialises the decoy, stages into its index and commits onto its
    branch."""
    clean = {key: value for key, value in os.environ.items()
             if not is_git_redirecting_variable(key)}

    def decoy_git(*args):
        return subprocess.run(["git", "-c", "user.name=d", "-c", "user.email=d@invalid", *args],
                              cwd=decoy, env=clean, check=True, capture_output=True,
                              timeout=60).stdout

    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "tracked.txt").write_text("decoy\n", encoding="utf-8")
    decoy_git("init", "-q")
    decoy_git("add", "tracked.txt")
    decoy_git("commit", "-qm", "decoy")
    # The shell's own unstaged work, which a misdirected `git add -A` would stage.
    (decoy / "untracked.txt").write_text("work in progress\n", encoding="utf-8")
    before = (decoy_git("rev-parse", "HEAD"), decoy_git("status", "--porcelain"),
              (decoy / ".git" / "index").read_bytes())

    rig = tmp_path / "rig"
    rig.mkdir()
    (rig / "conftest.py").write_text(
        _GIT_RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    (rig / "test_git_rig.py").write_text(_GIT_RIG_TESTS, encoding="utf-8")
    # No PYTEST_* name from the shell that runs the suite reaches the child.
    env = {key: value for key, value in clean.items() if not key.startswith("PYTEST_")}
    env.update(_shell_exports(decoy)[row])
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider",
         "-p", "no:randomly", "test_git_rig.py"],
        cwd=rig, env=env, capture_output=True, text=True, timeout=300)
    output = proc.stdout + proc.stderr
    after = (decoy_git("rev-parse", "HEAD"), decoy_git("status", "--porcelain"),
             (decoy / ".git" / "index").read_bytes())
    assert after == before, ("the child's rig changed the decoy repository", before, after, output)
    assert _summary_counts(output) == {"passed": 1}, output
    assert proc.returncode == 0, output


_XDG_RIG_TESTS = '''
import os
import subprocess


def test_the_operators_xdg_git_config_is_not_read(tmp_path):
    got = subprocess.run(["git", "config", "--get", "alias.zz"], cwd=tmp_path,
                         capture_output=True, text=True, timeout=60)
    assert got.stdout.strip() == "", got.stdout
    assert "XDG_CONFIG_HOME" not in os.environ
'''


def test_a_test_cannot_read_the_git_config_the_shell_points_xdg_config_home_at(tmp_path):
    """A child session whose shell exports XDG_CONFIG_HOME at a directory whose
    git/config defines an alias. Its test asks git for that alias. MUTANT:
    tests/conftest.py keeps XDG_CONFIG_HOME. git then reads the operator's
    config from there despite the sandbox HOME and GIT_CONFIG_NOSYSTEM, and
    the alias reaches the test (and merge-guard alias lookups)."""
    xdg = tmp_path / "xdg"
    (xdg / "git").mkdir(parents=True)
    (xdg / "git" / "config").write_text("[alias]\n\tzz = status\n", encoding="utf-8")
    clean = {key: value for key, value in os.environ.items()
             if not is_git_redirecting_variable(key)}
    control = subprocess.run(["git", "config", "--get", "alias.zz"], cwd=tmp_path,
                             env={**clean, "XDG_CONFIG_HOME": str(xdg)},
                             capture_output=True, text=True, timeout=60)
    assert control.stdout.strip() == "status", "the planted alias is not readable at all"
    rig = tmp_path / "rig"
    rig.mkdir()
    (rig / "conftest.py").write_text(
        _GIT_RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    (rig / "test_xdg_rig.py").write_text(_XDG_RIG_TESTS, encoding="utf-8")
    env = {key: value for key, value in clean.items() if not key.startswith("PYTEST_")}
    env["XDG_CONFIG_HOME"] = str(xdg)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider",
         "-p", "no:randomly", "test_xdg_rig.py"],
        cwd=rig, env=env, capture_output=True, text=True, timeout=300)
    output = proc.stdout + proc.stderr
    assert _summary_counts(output) == {"passed": 1}, output
    assert proc.returncode == 0, output


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


def _summary_counts(output):
    """pytest's final summary as {outcome: count}, such as {"passed": 2,
    "error": 1, "warning": 1}; the plural keys errors and warnings read as
    error and warning."""
    summary = None
    for line in output.splitlines():
        found = re.match(r"^=*\s*(\d+ [a-z]+(?:, \d+ [a-z]+)*) in [\d.]+s", line)
        if found:
            summary = found.group(1)
    assert summary is not None, f"no pytest summary line in:\n{output}"
    singular = {"errors": "error", "warnings": "warning"}
    return {singular.get(key, key): int(count)
            for count, key in re.findall(r"(\d+) ([a-z]+)", summary)}


def test_the_conftest_fails_a_test_that_leaves_a_marker_under_an_importer_id(tmp_path):
    (tmp_path / "conftest.py").write_text(
        _MARKER_RIG_CONFTEST.format(conftest=str(_CONFTEST)), encoding="utf-8")
    (tmp_path / "test_marker_rig.py").write_text(_MARKER_RIG_TESTS, encoding="utf-8")
    # No PYTEST_* name from the shell that runs the suite reaches the child.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
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
        # shows as one of the two passes and as the one error. Read by key, so a
        # counted warning or another outcome does not break the match.
        counts = _summary_counts(output)
        assert counts.get("passed") == 2 and counts.get("error") == 1, (counts, output)
        assert proc.returncode == 1, output
        assert ("ERROR test_marker_rig.py::test_leaves_a_marker_under_an_importer_id"
                in output), output
        assert "fixtures.session_marker.fresh_session_id" in output, output
        assert not marker.exists(), "the guard failed the test but left its marker behind"
    finally:
        if record.exists():
            embedding_marker(record.read_text()).unlink(missing_ok=True)
