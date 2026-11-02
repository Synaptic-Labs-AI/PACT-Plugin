"""The three prompt-time Current Session block writers use the ONE document.

Location: pact-plugin/tests/test_block_writers_one_document.py

bootstrap_prompt_gate (the unrecorded-lead block rewrite),
bootstrap_marker_writer (the team-name write-back) and realign_team (the
`- Team:` line) each take one `session_memory_root()` observation and write
the document `resolve_managed_document` names for it.

The separating layout is a lead declared in a SUBDIRECTORY of a main checkout
whose only document is at the toplevel: the one resolver reaches it through
its enclosing-checkout rung, while a writer that resolves the raw root finds
no file there and writes nothing. Revert check for every arm: with the writer
on `resolve_project_claude_md_path(root)` the toplevel block keeps the old
team and the arm goes red.

Real `git init` repositories under tmp_path, no remote.
"""
from __future__ import annotations

from test_worktree_git_failure_skip import (  # noqa: F401  # `repo` is a fixture
    LEAD,
    SESSION_ID,
    _patch_bootstrap_context,
    _prompt_gate_frame,
    _session_with_session_start,
    repo,
)
from helpers import doc_at  # noqa: E402


def _subdirectory_lead(repo, monkeypatch, session_dir):
    """A lead declared in `<repo>/pkg`, whose only document is the
    toplevel's, carrying a Current Session block that names the old team."""
    from shared.session_resume import update_session_info

    (repo / ".claude").mkdir()
    assert update_session_info(SESSION_ID, "old-team", str(session_dir), "/plugin", doc=doc_at(repo))
    subdir = repo / "pkg"
    subdir.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(subdir))
    monkeypatch.chdir(subdir)
    return repo / ".claude" / "CLAUDE.md", subdir


def _assert_toplevel_written(toplevel_doc, subdir):
    assert "session-new" in toplevel_doc.read_text(encoding="utf-8"), (
        "the writer did not reach the toplevel document"
    )
    assert not (subdir / ".claude").exists(), "a subdirectory document appeared"
    assert not (subdir / "CLAUDE.md").exists()


def test_the_prompt_gate_rewrites_the_toplevel_block(repo, monkeypatch):
    import bootstrap_prompt_gate as writer

    session_dir = _session_with_session_start(repo)
    toplevel_doc, subdir = _subdirectory_lead(repo, monkeypatch, session_dir)
    monkeypatch.setattr(writer.pact_context, "get_team_name", lambda: "session-new")
    monkeypatch.setattr(writer.pact_context, "get_plugin_root", lambda: "/plugin")
    monkeypatch.setattr(writer.pact_context, "get_lead_address", lambda: "lead")

    writer._record_unrecorded_lead(_prompt_gate_frame(), str(session_dir))

    _assert_toplevel_written(toplevel_doc, subdir)


def test_the_marker_write_back_updates_the_toplevel_block(repo, monkeypatch):
    import bootstrap_marker_writer as writer

    session_dir = _session_with_session_start(repo)
    toplevel_doc, subdir = _subdirectory_lead(repo, monkeypatch, session_dir)
    _patch_bootstrap_context(monkeypatch, writer, subdir, session_dir)

    status = writer._write_back_aligned_team_name({"agent_type": LEAD})

    assert status is None, status
    _assert_toplevel_written(toplevel_doc, subdir)


def test_the_realign_line_updates_the_toplevel_block(repo, monkeypatch):
    from shared.realign_team import _write_team_line

    session_dir = _session_with_session_start(repo)
    toplevel_doc, subdir = _subdirectory_lead(repo, monkeypatch, session_dir)

    _write_team_line("sid", "session-new", str(session_dir), "/plugin")

    _assert_toplevel_written(toplevel_doc, subdir)


# ---------------------------------------------------------------------------
# The declared-only permission: a frame the platform handed no project dir
# rewrites no block, even when its cwd is a known root holding a document.
# Revert check for every arm: without the writer's `session.declared` guard
# the cwd anchor resolves this document and the arm sees it rewritten.
# ---------------------------------------------------------------------------


def _undeclared_lead_at(repo, monkeypatch, session_dir):
    from shared.session_resume import update_session_info

    (repo / ".claude").mkdir()
    assert update_session_info(SESSION_ID, "old-team", str(session_dir), "/plugin", doc=doc_at(repo))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(repo)
    doc = repo / ".claude" / "CLAUDE.md"
    return doc, doc.read_bytes()


def test_an_undeclared_prompt_gate_frame_rewrites_no_block(repo, monkeypatch):
    import bootstrap_prompt_gate as writer

    session_dir = _session_with_session_start(repo)
    doc, before = _undeclared_lead_at(repo, monkeypatch, session_dir)
    monkeypatch.setattr(writer.pact_context, "get_team_name", lambda: "session-new")
    monkeypatch.setattr(writer.pact_context, "get_plugin_root", lambda: "/plugin")
    monkeypatch.setattr(writer.pact_context, "get_lead_address", lambda: "lead")

    writer._record_unrecorded_lead(_prompt_gate_frame(), str(session_dir))

    assert doc.read_bytes() == before


def test_an_undeclared_marker_write_back_writes_no_block(repo, monkeypatch):
    import bootstrap_marker_writer as writer

    session_dir = _session_with_session_start(repo)
    doc, before = _undeclared_lead_at(repo, monkeypatch, session_dir)
    _patch_bootstrap_context(monkeypatch, writer, repo, session_dir)

    assert writer._write_back_aligned_team_name({"agent_type": LEAD}) is None

    assert doc.read_bytes() == before


def test_an_undeclared_realign_writes_no_block(repo, monkeypatch):
    from shared.realign_team import _write_team_line

    session_dir = _session_with_session_start(repo)
    doc, before = _undeclared_lead_at(repo, monkeypatch, session_dir)

    _write_team_line("sid", "session-new", str(session_dir), "/plugin")

    assert doc.read_bytes() == before
