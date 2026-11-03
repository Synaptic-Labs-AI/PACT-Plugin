"""Session-state write failures surface on the user-visible channel.

Location: pact-plugin/tests/test_session_state_reporting.py
Summary: A SessionStart whose session directory cannot be written used to
    report only on hook stderr, which the platform does not show, while the
    project-document failure in the same run surfaced on systemMessage. These
    arms pin the sibling treatment: one message naming each lost write, no
    message when both writes succeed.

Used by/with: hooks/session_init.py (the report), hooks/shared/pact_context.py
    (`persist_context` returns its outcome), hooks/shared/session_journal.py
    (`append_event` already does).
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import session_init
from shared.pact_context import _build_session_path, project_slug

LEAD = "PACT:pact-orchestrator"
SID = "dddddddd-eeee-ffff-0000-222222222222"


def _run(monkeypatch, project_dir) -> str:
    """Drive SessionStart for a lead frame and return its systemMessage."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.chdir(project_dir)
    stdin_data = json.dumps(
        {"session_id": SID, "source": "startup", "agent_type": LEAD}
    )
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    return json.loads(mock_stdout.getvalue()).get("systemMessage", "")


def _session_dir(project_dir) -> Path:
    return _build_session_path(project_slug(str(project_dir)), SID)


def test_a_cold_unwritable_session_dir_names_both_losses(tmp_path, monkeypatch):
    """Neither file can be created: the message must say both were lost, or
    the user is told to use a session directory that holds nothing."""
    project = tmp_path / "project"
    project.mkdir()
    session_dir = _session_dir(project)
    session_dir.mkdir(parents=True)
    session_dir.chmod(0o555)
    try:
        message = _run(monkeypatch, project)
    finally:
        session_dir.chmod(0o755)

    assert "could not write" in message, message
    assert "session context file" in message, message
    assert "session journal" in message, message


def test_an_unwritable_journal_names_only_the_journal(tmp_path, monkeypatch):
    """MID-SESSION SHAPE. The directory is writable and the context write
    succeeds; an existing read-only journal is the member that cannot be
    written. The message must name the journal and not claim the context was
    lost too — the two writes fail separately."""
    project = tmp_path / "project"
    project.mkdir()
    session_dir = _session_dir(project)
    session_dir.mkdir(parents=True)
    journal = session_dir / "session-journal.jsonl"
    journal.write_text("", encoding="utf-8")
    journal.chmod(0o444)
    try:
        message = _run(monkeypatch, project)
    finally:
        journal.chmod(0o644)

    assert "could not write" in message, message
    assert "session journal" in message, message
    assert "session context file" not in message, message


def test_a_writable_session_dir_reports_nothing(tmp_path, monkeypatch):
    """THE NO-NOISE CONTROL. Without it the arm above passes on a hook that
    always warns."""
    project = tmp_path / "project"
    project.mkdir()

    message = _run(monkeypatch, project)

    assert "could not write" not in message, message
