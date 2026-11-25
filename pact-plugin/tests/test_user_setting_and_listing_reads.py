"""
Location: pact-plugin/tests/test_user_setting_and_listing_reads.py
Summary: The user-scope `config.json` is read like the other identity inputs
(a regular file only, never through a FIFO or a symlink, size-capped), and the
directory-listing memo notices an entry added within one mtime tick.
Used by: pytest; exercises project_identity.read_user_setting and
memory_api._listing on real files.
"""

from __future__ import annotations

import json
import os
import signal

import pytest

from scripts import memory_api
from scripts import project_identity as pi

ID = "github.com/me/proj"


@pytest.fixture
def alarm():
    """Fail rather than hang when a read blocks. `pytest.fail` raises an
    exception no `except OSError` in the reader can swallow."""
    def expire(signum, frame):
        pytest.fail("the read blocked on the FIFO")

    previous = signal.signal(signal.SIGALRM, expire)
    signal.alarm(5)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, previous)


def test_a_regular_config_is_read(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"project_id": ID}), encoding="utf-8")
    assert pi.read_user_setting(config) == ID


def test_a_fifo_config_is_refused_without_blocking(tmp_path, alarm):
    config = tmp_path / "config.json"
    os.mkfifo(config)
    assert pi.read_user_setting(config) is None


def test_a_symlinked_config_is_refused(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"project_id": ID}), encoding="utf-8")
    (tmp_path / "config.json").symlink_to(target)
    assert pi.read_user_setting(tmp_path / "config.json") is None


def test_an_oversized_config_is_refused(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"project_id": ID, "pad": "x" * (2 << 20)}), encoding="utf-8")
    assert pi.read_user_setting(config) is None


def test_the_listing_sees_an_entry_added_within_one_mtime_tick(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_api, "_DIR_LISTINGS", {})
    folder = tmp_path / "sessions"
    folder.mkdir()
    (folder / "a").mkdir()
    before = os.stat(folder)
    assert memory_api._listing(folder) == {"a"}
    (folder / "b").mkdir()
    # A coarse-mtime filesystem: the add lands in the same mtime tick.
    os.utime(folder, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert os.stat(folder).st_mtime_ns == before.st_mtime_ns, "precondition"
    assert memory_api._listing(folder) == {"a", "b"}
