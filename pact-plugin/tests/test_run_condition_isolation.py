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
