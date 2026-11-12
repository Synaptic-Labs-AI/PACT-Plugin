"""
Location: pact-plugin/tests/test_run_condition_isolation.py
Summary: Pins the session-wide isolation tests/conftest.py sets up so that one
         run's outcome does not depend on what an earlier run or the machine
         left behind.
Used by: the suite; the settings under test live in tests/conftest.py.
"""
import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


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


@pytest.mark.parametrize("round_", [1, 2])
def test_the_merge_guard_git_caches_start_empty(request, tmp_path, round_):
    import shared.merge_guard_common as merge_guard_common

    assert "_reset_merge_guard_git_caches" in request.fixturenames
    caches = (merge_guard_common._git_config_aliases, merge_guard_common._git_commands)
    assert [cache.cache_info().currsize for cache in caches] == [0, 0]
    # Fill both, so whichever test runs next in this process sees them cleared.
    merge_guard_common._git_config_aliases(str(tmp_path))
    merge_guard_common._git_commands(str(tmp_path))
    assert all(cache.cache_info().currsize for cache in caches)
