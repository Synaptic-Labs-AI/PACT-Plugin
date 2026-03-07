"""
Tests for shared/session_resume.py -- session resume and snapshot management.

Tests cover:
update_session_info():
1. Returns None when CLAUDE_PROJECT_DIR not set
2. Returns None when project CLAUDE.md doesn't exist
3. Replaces existing session block between markers
4. Inserts session block before "## Retrieved Context" when no markers
5. Appends session block at end as fallback

restore_last_session():
6. Returns None when no snapshot file exists
7. Returns content with header if file exists
8. Rotates file to last-session.prev.md
9. Returns None when project_slug is empty
10. Returns None when snapshot file is empty

check_resumption_context():
11. Returns None when no in_progress or pending tasks
12. Returns feature task names
13. Returns phase names
14. Returns agent count
15. Returns blocker count with bold formatting
16. Mixed task types
"""

import sys
from pathlib import Path

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestUpdateSessionInfo:
    """Tests for update_session_info() -- session info in project CLAUDE.md."""

    def test_returns_none_when_no_project_dir(self, monkeypatch):
        """Should return None when CLAUDE_PROJECT_DIR not set."""
        from shared.session_resume import update_session_info

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        result = update_session_info("session-123", "pact-session1")

        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path, monkeypatch):
        """Should return None when project CLAUDE.md doesn't exist."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_session_info("session-123", "pact-session1")

        assert result is None

    def test_replaces_existing_session_block(self, tmp_path, monkeypatch):
        """Should replace content between session markers."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / "CLAUDE.md"
        target.write_text(
            "# Project\n\n"
            "<!-- SESSION_START -->\n"
            "## Current Session\nOld session info\n"
            "<!-- SESSION_END -->\n\n"
            "## Other Section\n"
        )

        result = update_session_info("new-session-id", "pact-newsess")

        assert result == "Session info updated in project CLAUDE.md"
        content = target.read_text()
        assert "new-session-id" in content
        assert "pact-newsess" in content
        assert "Old session info" not in content
        assert "## Other Section" in content

    def test_inserts_before_retrieved_context(self, tmp_path, monkeypatch):
        """Should insert session block before '## Retrieved Context' when no markers."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Project\n\n## Retrieved Context\nSome context\n")

        result = update_session_info("sess-abc", "pact-sessabc")

        assert result == "Session info added to project CLAUDE.md"
        content = target.read_text()
        assert "sess-abc" in content
        assert "pact-sessabc" in content
        # Session block should come before Retrieved Context
        session_pos = content.index("<!-- SESSION_START -->")
        context_pos = content.index("## Retrieved Context")
        assert session_pos < context_pos

    def test_appends_at_end_as_fallback(self, tmp_path, monkeypatch):
        """Should append session block when no markers or Retrieved Context."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Project\n\nSome content\n")

        result = update_session_info("sess-xyz", "pact-sessxyz")

        assert result == "Session info added to project CLAUDE.md"
        content = target.read_text()
        assert "sess-xyz" in content
        assert "<!-- SESSION_START -->" in content


class TestRestoreLastSession:
    """Tests for restore_last_session() -- cross-session continuity."""

    def test_returns_none_when_no_snapshot(self, tmp_path):
        """Should return None when no last-session.md exists."""
        from shared.session_resume import restore_last_session

        result = restore_last_session(
            project_slug="nonexistent",
            sessions_dir=str(tmp_path),
        )

        assert result is None

    def test_returns_content_with_header(self, tmp_path):
        """Should return snapshot content with descriptive header."""
        from shared.session_resume import restore_last_session

        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        snapshot = "# Last Session\n## Completed Tasks\n- #1 auth\n"
        (proj_dir / "last-session.md").write_text(snapshot)

        result = restore_last_session(
            project_slug="my-project",
            sessions_dir=str(tmp_path),
        )

        assert result is not None
        assert "Previous session summary" in result
        assert "read-only reference" in result
        assert "# Last Session" in result

    def test_rotates_file_to_prev(self, tmp_path):
        """Should move last-session.md to last-session.prev.md after reading."""
        from shared.session_resume import restore_last_session

        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        content = "# Last Session\n"
        (proj_dir / "last-session.md").write_text(content)

        restore_last_session(
            project_slug="my-project",
            sessions_dir=str(tmp_path),
        )

        assert not (proj_dir / "last-session.md").exists()
        assert (proj_dir / "last-session.prev.md").exists()
        assert (proj_dir / "last-session.prev.md").read_text() == content

    def test_returns_none_when_empty_slug(self, tmp_path):
        """Should return None when project_slug is empty."""
        from shared.session_resume import restore_last_session

        result = restore_last_session(
            project_slug="",
            sessions_dir=str(tmp_path),
        )

        assert result is None

    def test_returns_none_when_empty_file(self, tmp_path):
        """Should return None when snapshot file is empty."""
        from shared.session_resume import restore_last_session

        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        (proj_dir / "last-session.md").write_text("")

        result = restore_last_session(
            project_slug="my-project",
            sessions_dir=str(tmp_path),
        )

        assert result is None


class TestCheckResumptionContext:
    """Tests for check_resumption_context() -- resumption detection."""

    def test_returns_none_when_no_active_tasks(self):
        """Should return None when all tasks are completed."""
        from shared.session_resume import check_resumption_context

        tasks = [
            {"id": "1", "subject": "auth feature", "status": "completed", "metadata": {}},
        ]

        result = check_resumption_context(tasks)

        assert result is None

    def test_returns_none_when_empty_list(self):
        """Should return None for empty task list."""
        from shared.session_resume import check_resumption_context

        result = check_resumption_context([])

        assert result is None

    def test_returns_feature_task_names(self):
        """Should include feature task names in resumption context."""
        from shared.session_resume import check_resumption_context

        tasks = [
            {"id": "1", "subject": "Implement auth system", "status": "in_progress", "metadata": {}},
        ]

        result = check_resumption_context(tasks)

        assert result is not None
        assert "Features:" in result
        assert "Implement auth system" in result

    def test_returns_phase_names(self):
        """Should include phase names in resumption context."""
        from shared.session_resume import check_resumption_context

        tasks = [
            {"id": "2", "subject": "ARCHITECT: design", "status": "in_progress", "metadata": {}},
        ]

        result = check_resumption_context(tasks)

        assert result is not None
        assert "Phases:" in result
        assert "ARCHITECT" in result

    def test_returns_agent_count(self):
        """Should include count of active agents."""
        from shared.session_resume import check_resumption_context

        tasks = [
            {"id": "3", "subject": "pact-backend-coder", "status": "in_progress", "metadata": {}},
            {"id": "4", "subject": "pact-frontend-coder", "status": "in_progress", "metadata": {}},
        ]

        result = check_resumption_context(tasks)

        assert result is not None
        assert "Active agents: 2" in result

    def test_returns_blocker_count(self):
        """Should include blocker count with bold formatting."""
        from shared.session_resume import check_resumption_context

        tasks = [
            {
                "id": "5",
                "subject": "BLOCKER: missing API key",
                "status": "in_progress",
                "metadata": {"type": "blocker"},
            },
        ]

        result = check_resumption_context(tasks)

        assert result is not None
        assert "**Blockers: 1**" in result

    def test_mixed_task_types(self):
        """Should handle mix of feature, phase, agent, and blocker tasks."""
        from shared.session_resume import check_resumption_context

        tasks = [
            {"id": "1", "subject": "Implement auth", "status": "in_progress", "metadata": {}},
            {"id": "2", "subject": "CODE: backend", "status": "in_progress", "metadata": {}},
            {"id": "3", "subject": "pact-backend-coder", "status": "in_progress", "metadata": {}},
            {
                "id": "4",
                "subject": "BLOCKER: missing key",
                "status": "in_progress",
                "metadata": {"type": "blocker"},
            },
            {"id": "5", "subject": "TEST: write tests", "status": "pending", "metadata": {}},
        ]

        result = check_resumption_context(tasks)

        assert result is not None
        assert "Features:" in result
        assert "Phases:" in result
        assert "Active agents: 1" in result
        assert "**Blockers: 1**" in result
        assert "(1 pending)" in result
