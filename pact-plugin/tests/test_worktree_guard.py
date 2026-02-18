# pact-plugin/tests/test_worktree_guard.py
"""
Tests for worktree_guard.py — PreToolUse hook matching Edit|Write that
blocks edits to application code outside the active worktree.

Tests cover:
1. Edit inside worktree → allow
2. Edit outside worktree to app code → block
3. Edit outside worktree to .claude/ → allow (AI tooling)
4. Edit outside worktree to docs/ → allow (documentation)
5. No PACT_WORKTREE_PATH set → allow (inactive, no-op)
6. CLAUDE.md always allowed
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestWorktreeGuard:
    """Tests for worktree_guard.check_worktree_boundary()."""

    def test_allows_edit_inside_worktree(self):
        from worktree_guard import check_worktree_boundary

        result = check_worktree_boundary(
            file_path="/tmp/worktrees/feat-auth/src/auth.ts",
            worktree_path="/tmp/worktrees/feat-auth"
        )
        assert result is None

    def test_blocks_app_code_outside_worktree(self):
        from worktree_guard import check_worktree_boundary

        result = check_worktree_boundary(
            file_path="/Users/mj/project/src/auth.ts",
            worktree_path="/tmp/worktrees/feat-auth"
        )
        assert result is not None
        assert "outside worktree" in result.lower()

    def test_allows_claude_dir_outside_worktree(self):
        from worktree_guard import check_worktree_boundary

        result = check_worktree_boundary(
            file_path="/Users/mj/.claude/CLAUDE.md",
            worktree_path="/tmp/worktrees/feat-auth"
        )
        assert result is None

    def test_allows_docs_outside_worktree(self):
        from worktree_guard import check_worktree_boundary

        result = check_worktree_boundary(
            file_path="/Users/mj/project/docs/architecture/design.md",
            worktree_path="/tmp/worktrees/feat-auth"
        )
        assert result is None

    def test_noop_when_no_worktree_path(self):
        from worktree_guard import check_worktree_boundary

        result = check_worktree_boundary(
            file_path="/Users/mj/project/src/auth.ts",
            worktree_path=""
        )
        assert result is None

    def test_allows_claude_md_anywhere(self):
        from worktree_guard import check_worktree_boundary

        result = check_worktree_boundary(
            file_path="/Users/mj/project/CLAUDE.md",
            worktree_path="/tmp/worktrees/feat-auth"
        )
        assert result is None
