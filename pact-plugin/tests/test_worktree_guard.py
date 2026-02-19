# pact-plugin/tests/test_worktree_guard.py
"""
Tests for worktree_guard.py — PreToolUse hook matching Edit|Write that
blocks edits to application code outside the active worktree.

Tests cover:
1. Edit inside worktree → allow
2. Edit outside worktree to app code → block with corrected path suggestion
3. Edit outside worktree to .claude/ → allow (AI tooling)
4. Edit outside worktree to docs/ → allow (documentation)
5. No PACT_WORKTREE_PATH set → allow (inactive, no-op)
6. CLAUDE.md always allowed
7. Corrected path suggestion (_suggest_worktree_path)
8. main() entry point: stdin JSON parsing, exit codes, output format
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

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
        assert "outside the active worktree" in result.lower()

    def test_block_message_includes_corrected_path(self, tmp_path):
        """Error message should include 'Did you mean:' with the corrected path."""
        from worktree_guard import check_worktree_boundary

        # Create a realistic directory structure so Path.resolve() works
        project_root = tmp_path / "project"
        worktree_dir = project_root / ".worktrees" / "feat-auth"
        src_dir = project_root / "src"
        worktree_dir.mkdir(parents=True)
        src_dir.mkdir(parents=True)
        (src_dir / "auth.ts").touch()

        result = check_worktree_boundary(
            file_path=str(src_dir / "auth.ts"),
            worktree_path=str(worktree_dir)
        )
        assert result is not None
        assert "Did you mean:" in result
        assert str(worktree_dir / "src" / "auth.ts") in result

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


class TestSuggestWorktreePath:
    """Tests for worktree_guard._suggest_worktree_path()."""

    def test_suggests_path_with_worktrees_dir(self, tmp_path):
        from worktree_guard import _suggest_worktree_path

        project_root = tmp_path / "project"
        worktree_dir = project_root / ".worktrees" / "feat-auth"
        src_dir = project_root / "src"
        worktree_dir.mkdir(parents=True)
        src_dir.mkdir(parents=True)
        (src_dir / "auth.ts").touch()

        result = _suggest_worktree_path(
            str(src_dir / "auth.ts"),
            str(worktree_dir)
        )
        assert result is not None
        assert result == str(worktree_dir / "src" / "auth.ts")

    def test_suggests_path_with_common_ancestor(self, tmp_path):
        """Fallback: uses common path prefix when .worktrees dir is absent."""
        from worktree_guard import _suggest_worktree_path

        # Two unrelated directories under same tmp_path
        dir_a = tmp_path / "workspace" / "main"
        dir_b = tmp_path / "workspace" / "branch"
        dir_a_src = dir_a / "src"
        dir_a_src.mkdir(parents=True)
        dir_b.mkdir(parents=True)
        (dir_a_src / "app.py").touch()

        result = _suggest_worktree_path(
            str(dir_a_src / "app.py"),
            str(dir_b)
        )
        # Should produce dir_b/main/src/app.py or similar via common ancestor
        assert result is not None
        assert str(dir_b) in result

    def test_returns_none_on_path_error(self):
        from worktree_guard import _suggest_worktree_path

        # Completely unresolvable paths (won't crash)
        result = _suggest_worktree_path("", "")
        # Empty paths produce empty Path parts, which may or may not suggest
        # Either None or a value is acceptable; must not raise
        assert result is None or isinstance(result, str)


class TestMainEntryPoint:
    """Tests for worktree_guard.main() stdin/stdout/exit behavior."""

    def test_main_exits_0_when_no_worktree_path(self):
        from worktree_guard import main

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_when_edit_inside_worktree(self):
        from worktree_guard import main

        input_data = json.dumps({
            "tool_input": {"file_path": "/tmp/worktrees/feat-auth/src/auth.ts"}
        })

        with patch.dict("os.environ", {"PACT_WORKTREE_PATH": "/tmp/worktrees/feat-auth"}), \
             patch("worktree_guard.check_worktree_boundary", return_value=None), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_2_when_edit_outside_worktree(self, capsys):
        from worktree_guard import main

        input_data = json.dumps({
            "tool_input": {"file_path": "/Users/mj/project/src/auth.ts"}
        })

        error_msg = "Edit blocked: /Users/mj/project/src/auth.ts is outside the active worktree"
        with patch.dict("os.environ", {"PACT_WORKTREE_PATH": "/tmp/worktrees/feat-auth"}), \
             patch("worktree_guard.check_worktree_boundary", return_value=error_msg), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_main_exits_0_on_invalid_json(self):
        from worktree_guard import main

        with patch.dict("os.environ", {"PACT_WORKTREE_PATH": "/tmp/worktrees/feat-auth"}), \
             patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_when_no_file_path(self):
        from worktree_guard import main

        input_data = json.dumps({"tool_input": {}})

        with patch.dict("os.environ", {"PACT_WORKTREE_PATH": "/tmp/worktrees/feat-auth"}), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
