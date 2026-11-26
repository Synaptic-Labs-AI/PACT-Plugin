"""
Location: pact-plugin/tests/test_user_setting_and_listing_reads.py
Summary: The user-scope `config.json` is read like the other identity inputs
(a regular file only, never through a FIFO or a symlink, size-capped), and the
directory-listing and prune-ledger memos notice a change within one mtime tick.
Used by: pytest; exercises project_identity.read_user_setting,
memory_api._listing and memory_api._pruned_session_index on real files.
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


NESTED = '{"project_id": ' + "[" * 100_000 + "]" * 100_000 + "}"


def test_a_deeply_nested_config_is_skipped(tmp_path):
    """A config whose JSON nests past Python's recursion limit is skipped,
    as the setting promises (never raises). RED WHEN json.loads's
    RecursionError escapes and every resolution reaching the setting fails."""
    config = tmp_path / "config.json"
    config.write_text(NESTED, encoding="utf-8")
    assert pi.read_user_setting(config) is None


def test_a_deeply_nested_session_record_reads_as_empty(tmp_path, monkeypatch):
    """The session record reader (worktree identity, session context) answers
    {} for a record nested past the recursion limit. RED WHEN the
    RecursionError escapes."""
    from shared import project_scope

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    record = tmp_path / "pact-sessions" / "proj" / "sess" / "pact-session-context.json"
    record.parent.mkdir(parents=True)
    record.write_text(NESTED, encoding="utf-8")
    assert project_scope._session_record_on_disk("sess", "pact-session-context.json") == {}


def test_the_ledger_index_sees_a_same_size_file_renamed_over_it(tmp_path, monkeypatch):
    """A compaction renames a new file over the ledger; one of the same size
    whose mtime lands in the old tick must not be served the old index."""
    monkeypatch.setattr(memory_api, "_PRUNED_SESSION_INDEX", {})
    ledger = tmp_path / "ledger"
    ledger.write_text('{"session_id": "a1"}\n', encoding="utf-8")
    before = os.stat(ledger)
    assert set(memory_api._pruned_session_index(ledger)) == {"a1"}
    replacement = tmp_path / "ledger.new"
    replacement.write_text('{"session_id": "b2"}\n', encoding="utf-8")
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(replacement, ledger)
    after = os.stat(ledger)
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns), "precondition"
    assert set(memory_api._pruned_session_index(ledger)) == {"b2"}


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
