"""
Tests for merge guard hooks — merge_guard_post.py and merge_guard_pre.py.

Tests cover:
1. merge_guard_post: keyword detection in AskUserQuestion text
2. merge_guard_post: affirmative answer detection
3. merge_guard_post: token file creation with correct structure and permissions
4. merge_guard_post: context extraction (PR numbers, branch names)
5. merge_guard_post: non-merge questions are ignored
6. merge_guard_post: negative answers don't create tokens
7. merge_guard_pre: dangerous command detection
8. merge_guard_pre: valid token allows commands
9. merge_guard_pre: missing token blocks commands
10. merge_guard_pre: expired token blocks commands and gets cleaned up
11. merge_guard_pre: safe commands pass through
12. main() entry points: stdin JSON, exit codes, output format
"""

import io
import json
import os
import stat
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# =============================================================================
# merge_guard_post.py tests
# =============================================================================


class TestIsMergeQuestion:
    """Tests for merge_guard_post.is_merge_question()."""

    def test_detects_merge_keyword(self):
        from merge_guard_post import is_merge_question

        assert is_merge_question("Do you want to merge this PR?")

    def test_detects_force_push(self):
        from merge_guard_post import is_merge_question

        assert is_merge_question("Should I force push to main?")

    def test_detects_force_push_hyphenated(self):
        from merge_guard_post import is_merge_question

        assert is_merge_question("Should I force-push to main?")

    def test_detects_delete_branch(self):
        from merge_guard_post import is_merge_question

        assert is_merge_question("Should I delete branch feat/test?")

    def test_detects_branch_d_flag(self):
        from merge_guard_post import is_merge_question

        assert is_merge_question("Run git branch -D feat/old?")

    def test_detects_git_push_f(self):
        from merge_guard_post import is_merge_question

        assert is_merge_question("Execute git push -f origin main?")

    def test_detects_branch_delete_force(self):
        from merge_guard_post import is_merge_question

        assert is_merge_question("Run git branch --delete --force feat/old?")

    def test_rejects_unrelated_question(self):
        from merge_guard_post import is_merge_question

        assert not is_merge_question("Should I add the logging middleware?")

    def test_rejects_empty_string(self):
        from merge_guard_post import is_merge_question

        assert not is_merge_question("")

    def test_case_insensitive(self):
        from merge_guard_post import is_merge_question

        assert is_merge_question("MERGE this PR now?")


class TestIsAffirmative:
    """Tests for merge_guard_post.is_affirmative()."""

    def test_yes(self):
        from merge_guard_post import is_affirmative

        assert is_affirmative("yes")

    def test_y(self):
        from merge_guard_post import is_affirmative

        assert is_affirmative("y")

    def test_confirm(self):
        from merge_guard_post import is_affirmative

        assert is_affirmative("confirm")

    def test_go_ahead(self):
        from merge_guard_post import is_affirmative

        assert is_affirmative("go ahead")

    def test_approved(self):
        from merge_guard_post import is_affirmative

        assert is_affirmative("approved")

    def test_proceed(self):
        from merge_guard_post import is_affirmative

        assert is_affirmative("proceed")

    def test_negative_no(self):
        from merge_guard_post import is_affirmative

        assert not is_affirmative("no")

    def test_negative_cancel(self):
        from merge_guard_post import is_affirmative

        assert not is_affirmative("cancel")

    def test_negative_empty(self):
        from merge_guard_post import is_affirmative

        assert not is_affirmative("")

    def test_with_leading_whitespace(self):
        from merge_guard_post import is_affirmative

        assert is_affirmative("  yes  ")

    def test_case_insensitive(self):
        from merge_guard_post import is_affirmative

        assert is_affirmative("YES")


class TestExtractContext:
    """Tests for merge_guard_post.extract_context()."""

    def test_extracts_pr_number_hash(self):
        from merge_guard_post import extract_context

        ctx = extract_context("Should I merge #42?")
        assert ctx["pr_number"] == "42"

    def test_extracts_pr_number_text(self):
        from merge_guard_post import extract_context

        ctx = extract_context("Should I merge PR 123?")
        assert ctx["pr_number"] == "123"

    def test_extracts_branch_name(self):
        from merge_guard_post import extract_context

        ctx = extract_context("Delete branch feat/my-feature?")
        assert ctx["branch"] == "feat/my-feature"

    def test_includes_question_snippet(self):
        from merge_guard_post import extract_context

        ctx = extract_context("Merge this?")
        assert "question_snippet" in ctx

    def test_snippet_truncated_at_200(self):
        from merge_guard_post import extract_context

        long_q = "merge " + "x" * 300
        ctx = extract_context(long_q)
        assert len(ctx["question_snippet"]) == 200

    def test_no_pr_number_when_absent(self):
        from merge_guard_post import extract_context

        ctx = extract_context("Should I merge this branch?")
        assert "pr_number" not in ctx


class TestWriteToken:
    """Tests for merge_guard_post.write_token()."""

    def test_creates_token_file(self, tmp_path):
        from merge_guard_post import write_token

        result = write_token({"test": True}, token_dir=tmp_path)
        assert result is not None
        assert Path(result).exists()

    def test_token_has_correct_structure(self, tmp_path):
        from merge_guard_post import write_token

        result = write_token({"test": True}, token_dir=tmp_path)
        with open(result) as f:
            data = json.load(f)

        assert "created_at" in data
        assert "expires_at" in data
        assert "context" in data
        assert data["context"]["test"] is True

    def test_token_has_correct_ttl(self, tmp_path):
        from merge_guard_post import write_token, TOKEN_TTL

        result = write_token({}, token_dir=tmp_path)
        with open(result) as f:
            data = json.load(f)

        assert data["expires_at"] - data["created_at"] == pytest.approx(TOKEN_TTL, abs=1)

    def test_token_file_permissions(self, tmp_path):
        from merge_guard_post import write_token

        result = write_token({}, token_dir=tmp_path)
        mode = os.stat(result).st_mode & 0o777
        assert mode == 0o600

    def test_token_filename_prefix(self, tmp_path):
        from merge_guard_post import write_token

        result = write_token({}, token_dir=tmp_path)
        assert "merge-authorized-" in Path(result).name


class TestPostMainEntryPoint:
    """Tests for merge_guard_post.main() stdin/exit behavior."""

    def test_main_exits_0_on_merge_approval(self, tmp_path):
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"question": "Should I merge #42?"},
            "tool_output": {"result": "yes"},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # Verify token was created
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1

    def test_main_exits_0_on_non_merge_question(self, tmp_path):
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"question": "Should I add logging?"},
            "tool_output": {"result": "yes"},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # No token should be created
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 0

    def test_main_exits_0_on_negative_answer(self, tmp_path):
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"question": "Should I merge #42?"},
            "tool_output": {"result": "no"},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 0

    def test_main_exits_0_on_invalid_json(self):
        from merge_guard_post import main

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_handles_string_tool_output(self, tmp_path):
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"question": "Should I merge?"},
            "tool_output": "yes",
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1


# =============================================================================
# merge_guard_pre.py tests
# =============================================================================


class TestIsDangerousCommand:
    """Tests for merge_guard_pre.is_dangerous_command()."""

    def test_gh_pr_merge(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr merge 42")

    def test_gh_pr_merge_with_flags(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr merge 42 --squash --delete-branch")

    def test_git_push_force(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push --force origin main")

    def test_git_push_f(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push -f origin main")

    def test_git_branch_D(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git branch -D feat/old")

    def test_git_branch_delete_force(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git branch --delete --force feat/old")

    def test_git_branch_force_delete(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git branch --force --delete feat/old")

    def test_safe_git_push(self):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("git push origin main")

    def test_safe_git_branch(self):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("git branch -d feat/old")

    def test_safe_git_status(self):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("git status")

    def test_safe_gh_pr_list(self):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr list")

    def test_safe_non_git_command(self):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("ls -la")

    def test_empty_command(self):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("")

    def test_git_push_combined_flags_with_f(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push -uf origin main")


class TestFindValidToken:
    """Tests for merge_guard_pre.find_valid_token()."""

    def test_finds_valid_token(self, tmp_path):
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {"test": True},
        }
        token_file = tmp_path / "merge-authorized-12345"
        token_file.write_text(json.dumps(token_data))

        result = find_valid_token(token_dir=tmp_path)
        assert result is not None
        assert result["context"]["test"] is True

    def test_returns_none_when_no_tokens(self, tmp_path):
        from merge_guard_pre import find_valid_token

        result = find_valid_token(token_dir=tmp_path)
        assert result is None

    def test_cleans_up_expired_token(self, tmp_path):
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now - 600,
            "expires_at": now - 300,  # Expired 5 min ago
            "context": {},
        }
        token_file = tmp_path / "merge-authorized-12345"
        token_file.write_text(json.dumps(token_data))

        result = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert not token_file.exists()  # Cleaned up

    def test_cleans_up_corrupted_token(self, tmp_path):
        from merge_guard_pre import find_valid_token

        token_file = tmp_path / "merge-authorized-12345"
        token_file.write_text("not json")

        result = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert not token_file.exists()

    def test_cleans_up_invalid_expiry(self, tmp_path):
        from merge_guard_pre import find_valid_token

        token_data = {
            "created_at": time.time(),
            "expires_at": "not-a-number",
            "context": {},
        }
        token_file = tmp_path / "merge-authorized-12345"
        token_file.write_text(json.dumps(token_data))

        result = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert not token_file.exists()

    def test_ignores_non_token_files(self, tmp_path):
        from merge_guard_pre import find_valid_token

        # Create a non-token file
        other_file = tmp_path / "some-other-file"
        other_file.write_text("not a token")

        result = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert other_file.exists()  # Not cleaned up


class TestCheckMergeAuthorization:
    """Tests for merge_guard_pre.check_merge_authorization()."""

    def test_allows_safe_commands(self, tmp_path):
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("git push origin main", token_dir=tmp_path)
        assert result is None

    def test_blocks_dangerous_without_token(self, tmp_path):
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "AskUserQuestion" in result

    def test_allows_dangerous_with_valid_token(self, tmp_path):
        from merge_guard_pre import check_merge_authorization

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
        }
        token_file = tmp_path / "merge-authorized-12345"
        token_file.write_text(json.dumps(token_data))

        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is None

    def test_blocks_dangerous_with_expired_token(self, tmp_path):
        from merge_guard_pre import check_merge_authorization

        now = time.time()
        token_data = {
            "created_at": now - 600,
            "expires_at": now - 300,
            "context": {},
        }
        token_file = tmp_path / "merge-authorized-12345"
        token_file.write_text(json.dumps(token_data))

        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "AskUserQuestion" in result


class TestPreMainEntryPoint:
    """Tests for merge_guard_pre.main() stdin/exit behavior."""

    def test_main_exits_0_on_safe_command(self):
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": "git status"}
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_2_on_dangerous_without_token(self, tmp_path, capsys):
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": "gh pr merge 42"}
        })

        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "AskUserQuestion" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_main_exits_0_on_dangerous_with_valid_token(self, tmp_path):
        from merge_guard_pre import main

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
        }
        token_file = tmp_path / "merge-authorized-12345"
        token_file.write_text(json.dumps(token_data))

        input_data = json.dumps({
            "tool_input": {"command": "gh pr merge 42"}
        })

        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_on_invalid_json(self):
        from merge_guard_pre import main

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_on_empty_command(self):
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": ""}
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_on_missing_command(self):
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {}
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0


# =============================================================================
# Integration: post writes token, pre reads it
# =============================================================================


class TestIntegration:
    """End-to-end: post hook writes token, pre hook reads it."""

    def test_approval_flow(self, tmp_path):
        """Full flow: user approves merge, then dangerous command is allowed."""
        from merge_guard_post import write_token, extract_context
        from merge_guard_pre import check_merge_authorization

        # Simulate post hook: user approved merge
        context = extract_context("Should I merge #42?")
        token_path = write_token(context, token_dir=tmp_path)
        assert token_path is not None

        # Simulate pre hook: dangerous command should be allowed
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is None  # Allowed

    def test_no_approval_flow(self, tmp_path):
        """Without approval, dangerous commands are blocked."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None  # Blocked
        assert "AskUserQuestion" in result
