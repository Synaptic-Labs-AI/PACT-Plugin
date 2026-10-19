"""
The session context file is not rewritten when its bytes would not change.

``persist_context`` writes by temp file + rename, which gives the file a new
inode even when the content is identical. A hook that rewrites an unchanged
record therefore looks like a change to anything watching the file, and a
compaction that keeps the right team should leave the record untouched.
These cells pin both directions: identical bytes keep the inode and mtime;
different bytes are written.
"""

import json

from fixtures.team_split import OLD_SID, stat_snapshot


def _target_and_context(tmp_path, monkeypatch, team_name):
    import shared.pact_context as ctx_module
    project_dir = tmp_path / "project"
    project_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    ctx_module.reset_for_tests()
    return ctx_module.build_context_cache(
        team_name, OLD_SID, str(project_dir), str(tmp_path / "plugin"),
        started_at="2026-10-19T07:00:00+00:00",
    )


def test_identical_bytes_leave_the_file_untouched(tmp_path, monkeypatch):
    """Persisting the same context twice keeps the first write's inode and
    mtime: the second call does not touch the file."""
    import shared.pact_context as ctx_module
    target, context = _target_and_context(tmp_path, monkeypatch, "session-aaaa1111")
    ctx_module.persist_context(target, context)
    before = stat_snapshot(target)
    ctx_module.persist_context(target, dict(context))
    assert stat_snapshot(target) == before


def test_changed_bytes_are_written(tmp_path, monkeypatch):
    """Persisting a context whose team differs replaces the file's content:
    the skip applies only to identical bytes."""
    import shared.pact_context as ctx_module
    target, context = _target_and_context(tmp_path, monkeypatch, "session-0001639f")
    ctx_module.persist_context(target, context)
    changed = dict(context, team_name="session-aaaa1111")
    ctx_module.persist_context(target, changed)
    assert json.loads(target.read_text(encoding="utf-8"))["team_name"] == (
        "session-aaaa1111"
    )


def test_an_absent_file_is_created(tmp_path, monkeypatch):
    """The first persist creates the file with the context's bytes."""
    import shared.pact_context as ctx_module
    target, context = _target_and_context(tmp_path, monkeypatch, "session-aaaa1111")
    assert not target.exists()
    ctx_module.persist_context(target, context)
    assert json.loads(target.read_text(encoding="utf-8")) == context
