"""
Location: pact-plugin/tests/test_merge_guard_vendored_baselines.py
Summary: pins load_vendored, which loads the vendored merge_guard_common.py
         bases that the baked-SHA certification files certify against.
Used by: the suite. Path setup is conftest-owned; see tests/test_path_setup_pin.py.

A fixture whose bytes drifted from its pinned git blob id is no longer the
commit the cert names, so load_vendored must fail loudly on it, never load it.
"""

import pytest

import merge_guard_baseline_loader as loader


def _copy_fixture(tmp_path, key, change_one_byte):
    source = loader._VENDORED_DIR / "merge_guard_common_b4041ccf.py"
    data = source.read_bytes()
    pinned = loader._git_blob_id(data)
    if change_one_byte:
        data = data[:-1] + (b"#" if data[-1:] != b"#" else b" ")
    copy = tmp_path / ("merge_guard_common_%s.py" % key)
    copy.write_bytes(data)
    return copy, pinned


def test_the_loader_fails_loudly_on_a_drifted_fixture(tmp_path, monkeypatch):
    copy, pinned = _copy_fixture(tmp_path, "drift000", change_one_byte=True)
    monkeypatch.setattr(loader, "_VENDORED_DIR", tmp_path)
    monkeypatch.setitem(loader._VENDORED, "drift000", (copy.name, pinned))
    with pytest.raises(pytest.fail.Exception, match="git blob id mismatch"):
        loader.load_vendored("drift000")


def test_the_loader_loads_an_intact_copy(tmp_path, monkeypatch):
    """The control for the arm above: the same setup with the bytes unchanged
    loads, so that arm fails on the drift and not on the setup."""
    copy, pinned = _copy_fixture(tmp_path, "intact00", change_one_byte=False)
    monkeypatch.setattr(loader, "_VENDORED_DIR", tmp_path)
    monkeypatch.setitem(loader._VENDORED, "intact00", (copy.name, pinned))
    module = loader.load_vendored("intact00")
    assert callable(module.is_dangerous_command)
