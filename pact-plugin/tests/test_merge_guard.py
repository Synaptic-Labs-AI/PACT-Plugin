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
13. Adversarial: command obfuscation, shell escaping, multi-command strings
14. Edge cases: boundary TTL, malformed tokens, missing fields, empty inputs
15. Security: token permissions, token content validation, write failures
16. hooks.json: merge guard registration and sync flag validation
17. Integration: full main() flows, multiple tokens, token consumption
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
            "tool_input": {"questions": [{"question": "Should I merge #42?"}]},
            "tool_output": {"answers": {"Should I merge #42?": "yes"}},
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
            "tool_input": {"questions": [{"question": "Should I add logging?"}]},
            "tool_output": {"answers": {"Should I add logging?": "yes"}},
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
            "tool_input": {"questions": [{"question": "Should I merge #42?"}]},
            "tool_output": {"answers": {"Should I merge #42?": "no"}},
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
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
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

    def test_git_push_origin_main_is_dangerous(self):
        """git push origin main pushes directly to default branch — dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push origin main")

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

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is not None
        assert result["context"]["test"] is True
        assert path is not None
        assert "merge-authorized-12345" in path

    def test_returns_none_when_no_tokens(self, tmp_path):
        from merge_guard_pre import find_valid_token

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None

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

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
        assert not token_file.exists()  # Cleaned up

    def test_cleans_up_corrupted_token(self, tmp_path):
        from merge_guard_pre import find_valid_token

        token_file = tmp_path / "merge-authorized-12345"
        token_file.write_text("not json")

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
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

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
        assert not token_file.exists()

    def test_ignores_non_token_files(self, tmp_path):
        from merge_guard_pre import find_valid_token

        # Create a non-token file
        other_file = tmp_path / "some-other-file"
        other_file.write_text("not a token")

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
        assert other_file.exists()  # Not cleaned up


class TestCheckMergeAuthorization:
    """Tests for merge_guard_pre.check_merge_authorization()."""

    def test_allows_safe_commands(self, tmp_path):
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("git push origin feature/my-branch", token_dir=tmp_path)
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

    def test_approval_flow_via_main_entry_points(self, tmp_path):
        """Full flow using main() entry points for both hooks."""
        from merge_guard_post import main as post_main
        from merge_guard_pre import main as pre_main

        # Step 1: Post hook processes merge approval
        post_input = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge PR #99?"}]},
            "tool_output": {"answers": {"Should I merge PR #99?": "yes"}},
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(post_input)):
            with pytest.raises(SystemExit) as exc_info:
                post_main()
        assert exc_info.value.code == 0

        # Verify token was created
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1

        # Step 2: Pre hook allows the dangerous command
        pre_input = json.dumps({
            "tool_input": {"command": "gh pr merge 99 --squash"}
        })
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(pre_input)):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        assert exc_info.value.code == 0  # Allowed

    def test_multiple_valid_tokens(self, tmp_path):
        """Multiple valid tokens: each authorizes one operation (consumed on use)."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"op": "merge"}, token_dir=tmp_path)
        # Force a different filename by manipulating time
        with patch("merge_guard_post.time") as mock_time:
            mock_time.time.return_value = time.time() + 1
            write_token({"op": "force-push"}, token_dir=tmp_path)

        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) >= 2

        # First operation: consumes one token
        result1 = check_merge_authorization("gh pr merge 1", token_dir=tmp_path)
        assert result1 is None

        # Second operation: consumes the other token
        result2 = check_merge_authorization("git push --force origin main", token_dir=tmp_path)
        assert result2 is None

        # Third operation: blocked (all tokens consumed)
        result3 = check_merge_authorization("git branch -D old", token_dir=tmp_path)
        assert result3 is not None

    def test_expired_token_does_not_authorize(self, tmp_path):
        """Only expired tokens present — command should be blocked."""
        now = time.time()
        token_data = {
            "created_at": now - 600,
            "expires_at": now - 1,  # Just barely expired
            "context": {},
        }
        (tmp_path / "merge-authorized-99999").write_text(json.dumps(token_data))

        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        # Expired token should have been cleaned up
        assert not (tmp_path / "merge-authorized-99999").exists()


# =============================================================================
# Single-use token tests
# =============================================================================


class TestSingleUseToken:
    """Verify that tokens are consumed (deleted) after first use."""

    def test_token_deleted_after_authorization(self, tmp_path):
        """Token file is removed after it authorizes a command."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        token_path = write_token({"pr": "42"}, token_dir=tmp_path)
        assert token_path is not None
        assert Path(token_path).exists()

        # First command: allowed, token consumed
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is None  # Allowed
        assert not Path(token_path).exists()  # Token consumed

    def test_second_command_blocked_after_consumption(self, tmp_path):
        """Second dangerous command is blocked because token was consumed."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"pr": "42"}, token_dir=tmp_path)

        # First command: allowed
        result1 = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result1 is None

        # Second command: blocked (token consumed)
        result2 = check_merge_authorization("git push --force origin main", token_dir=tmp_path)
        assert result2 is not None
        assert "AskUserQuestion" in result2

    def test_safe_commands_do_not_consume_token(self, tmp_path):
        """Safe commands don't trigger token consumption."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        token_path = write_token({"pr": "42"}, token_dir=tmp_path)

        # Safe command: allowed without consuming token
        result = check_merge_authorization("git status", token_dir=tmp_path)
        assert result is None
        assert Path(token_path).exists()  # Token still present

    def test_consumption_does_not_interfere_with_expired_cleanup(self, tmp_path):
        """Expired tokens are cleaned up normally alongside consumption."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        now = time.time()

        # Create an expired token
        expired_data = {
            "created_at": now - 600,
            "expires_at": now - 300,
            "context": {},
        }
        expired_file = tmp_path / "merge-authorized-00001"
        expired_file.write_text(json.dumps(expired_data))

        # Create a valid token
        valid_path = write_token({"pr": "99"}, token_dir=tmp_path)

        # Authorize: expired cleaned up, valid consumed
        result = check_merge_authorization("gh pr merge 99", token_dir=tmp_path)
        assert result is None
        assert not expired_file.exists()  # Expired: cleaned up
        assert not Path(valid_path).exists()  # Valid: consumed

    def test_each_approval_authorizes_one_operation(self, tmp_path):
        """Two approvals authorize exactly two operations."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        # First approval
        write_token({"op": "merge"}, token_dir=tmp_path)
        result1 = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result1 is None  # Allowed

        # Blocked without new approval
        result2 = check_merge_authorization("gh pr merge 43", token_dir=tmp_path)
        assert result2 is not None  # Blocked

        # Second approval
        with patch("merge_guard_post.time") as mock_time:
            mock_time.time.return_value = time.time() + 1
            write_token({"op": "force-push"}, token_dir=tmp_path)
        result3 = check_merge_authorization("git push --force origin main", token_dir=tmp_path)
        assert result3 is None  # Allowed

        # Blocked again
        result4 = check_merge_authorization("git branch -D old", token_dir=tmp_path)
        assert result4 is not None  # Blocked

    def test_concurrent_deletion_is_safe(self, tmp_path):
        """If token is already deleted (race condition), authorization still works."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        token_path = write_token({"pr": "42"}, token_dir=tmp_path)

        # Simulate concurrent deletion: remove the token before check_merge_authorization
        # can consume it. The _safe_remove in consumption handles FileNotFoundError.
        os.unlink(token_path)

        # Command should be blocked because token no longer exists
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None


# =============================================================================
# Adversarial: command bypass attempts
# =============================================================================


class TestAdversarialCommandDetection:
    """Attempt to bypass dangerous command detection via shell tricks."""

    def test_command_in_subshell(self):
        """Dangerous command inside $() subshell is still caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("$(gh pr merge 42)")

    def test_command_after_semicolon(self):
        """Dangerous command after semicolon is caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("echo hello; gh pr merge 42")

    def test_command_after_pipe(self):
        """Dangerous command after pipe is caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("echo hello | gh pr merge 42")

    def test_command_after_and(self):
        """Dangerous command chained with && is caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("cd /tmp && gh pr merge 42")

    def test_command_after_or(self):
        """Dangerous command chained with || is caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("false || gh pr merge 42")

    def test_git_push_force_with_lease(self):
        """git push --force-with-lease is allowed — it's a safer alternative."""
        from merge_guard_pre import is_dangerous_command

        # --force-with-lease is intentionally excluded from force-push patterns
        # because it refuses to overwrite remote work not yet pulled locally.
        # Use a feature branch to isolate from "push to main" detection.
        assert not is_dangerous_command("git push --force-with-lease origin feature/my-branch")

    def test_git_push_force_with_remote_url(self):
        """Force push to explicit remote URL is caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "git push --force https://github.com/user/repo.git main"
        )

    def test_multiline_command_with_backslash_not_detected(self):
        """Backslash-continuation splits the pattern across lines — not detected.

        Known limitation: regex operates line-by-line. In practice, Claude Code
        sends commands as single-line strings, so this is acceptable.
        """
        from merge_guard_pre import is_dangerous_command

        # The \n breaks the regex match — documents a known limitation
        assert not is_dangerous_command("git push \\\n--force origin main")

    def test_git_push_force_via_config_flag_detected(self):
        """git -c ... push --force with interleaved -c flags IS detected."""
        from merge_guard_pre import is_dangerous_command

        # Patterns now handle optional -c flags between git and push
        assert is_dangerous_command("git -c push.default=current push --force origin")

    def test_safe_command_containing_merge_as_substring(self):
        """Commands that contain 'merge' as substring but aren't dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("git merge feature-branch")
        assert not is_dangerous_command("git mergetool")

    def test_safe_gh_pr_view(self):
        """gh pr view with merge-like URL is not dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr view 42")

    def test_safe_branch_lowercase_d(self):
        """git branch -d (lowercase) is safe — only -D is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("git branch -d old-branch")

    def test_dangerous_in_heredoc_style(self):
        """Dangerous command embedded in bash heredoc syntax."""
        from merge_guard_pre import is_dangerous_command

        # The command string still contains the dangerous pattern
        assert is_dangerous_command("bash -c 'gh pr merge 42'")

    def test_dangerous_with_env_var_prefix(self):
        """Dangerous command prefixed with env var assignment."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("GH_TOKEN=abc gh pr merge 42")

    def test_git_push_f_combined_with_other_flags(self):
        """git push with -f combined in various flag positions."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push -vf origin main")
        assert is_dangerous_command("git push -fu origin main")

    def test_git_branch_D_with_multiple_branches(self):
        """git branch -D with multiple branch args."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git branch -D branch1 branch2 branch3")


# =============================================================================
# Edge cases: merge_guard_post
# =============================================================================


class TestMergeQuestionEdgeCases:
    """Edge cases for merge question and affirmative detection."""

    def test_merge_keyword_at_word_boundary(self):
        """'emerged' should not trigger (merge is substring)."""
        from merge_guard_post import is_merge_question

        # Note: the regex uses re.search without \b, so 'emerged' WILL match.
        # This test documents the current behavior.
        result = is_merge_question("The data emerged from the pipeline")
        # Current regex matches 'merge' within 'emerged' — this is a known
        # trade-off: broader matching at the cost of rare false positives.
        assert result is True

    def test_affirmative_with_extra_text(self):
        """Affirmative followed by additional text."""
        from merge_guard_post import is_affirmative

        assert is_affirmative("yes please go ahead")

    def test_do_it(self):
        """'do it' is an affirmative pattern."""
        from merge_guard_post import is_affirmative

        assert is_affirmative("do it")

    def test_sure(self):
        """'sure' is affirmative."""
        from merge_guard_post import is_affirmative

        assert is_affirmative("sure")

    def test_okay(self):
        """'okay' is affirmative."""
        from merge_guard_post import is_affirmative

        assert is_affirmative("okay")

    def test_ok(self):
        """'ok' is affirmative."""
        from merge_guard_post import is_affirmative

        assert is_affirmative("ok")

    def test_yep(self):
        """'yep' is affirmative."""
        from merge_guard_post import is_affirmative

        assert is_affirmative("yep")

    def test_yeah(self):
        """'yeah' is affirmative."""
        from merge_guard_post import is_affirmative

        assert is_affirmative("yeah")

    def test_approve(self):
        """'approve' is affirmative."""
        from merge_guard_post import is_affirmative

        assert is_affirmative("approve")

    def test_not_affirmative_maybe(self):
        """'maybe' is NOT affirmative."""
        from merge_guard_post import is_affirmative

        assert not is_affirmative("maybe")

    def test_not_affirmative_let_me_think(self):
        """'let me think' is NOT affirmative."""
        from merge_guard_post import is_affirmative

        assert not is_affirmative("let me think about it")

    def test_not_affirmative_wait(self):
        """'wait' is NOT affirmative."""
        from merge_guard_post import is_affirmative

        assert not is_affirmative("wait")

    def test_not_affirmative_dont(self):
        """'don't' is NOT affirmative."""
        from merge_guard_post import is_affirmative

        assert not is_affirmative("don't do that")

    def test_extract_pull_request_text(self):
        """'pull request 456' extraction works."""
        from merge_guard_post import extract_context

        ctx = extract_context("Merge pull request 456 into main?")
        assert ctx["pr_number"] == "456"

    def test_extract_branch_with_dots(self):
        """Branch names with dots are extracted."""
        from merge_guard_post import extract_context

        ctx = extract_context("Delete branch release/v1.2.3?")
        assert ctx["branch"] == "release/v1.2.3"

    def test_extract_branch_with_underscores(self):
        """Branch names with underscores are extracted."""
        from merge_guard_post import extract_context

        ctx = extract_context("Merge feat/my_feature into main?")
        assert ctx["branch"] == "feat/my_feature"

    def test_extract_quoted_branch(self):
        """Branch names in quotes are extracted without quotes."""
        from merge_guard_post import extract_context

        ctx = extract_context("Delete branch 'old-feature'?")
        assert ctx["branch"] == "old-feature"


# =============================================================================
# Edge cases: token validation
# =============================================================================


class TestTokenEdgeCases:
    """Edge cases for token creation and validation."""

    def test_token_missing_expires_at(self, tmp_path):
        """Token with no expires_at field is treated as invalid."""
        from merge_guard_pre import find_valid_token

        token_data = {"created_at": time.time(), "context": {}}
        (tmp_path / "merge-authorized-11111").write_text(json.dumps(token_data))

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
        # Default expires_at=0 triggers the <= 0 check, token is cleaned up
        assert not (tmp_path / "merge-authorized-11111").exists()

    def test_token_negative_expiry(self, tmp_path):
        """Token with negative expires_at is invalid."""
        from merge_guard_pre import find_valid_token

        token_data = {
            "created_at": time.time(),
            "expires_at": -1,
            "context": {},
        }
        (tmp_path / "merge-authorized-22222").write_text(json.dumps(token_data))

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
        assert not (tmp_path / "merge-authorized-22222").exists()

    def test_token_zero_expiry(self, tmp_path):
        """Token with expires_at=0 is invalid."""
        from merge_guard_pre import find_valid_token

        token_data = {
            "created_at": time.time(),
            "expires_at": 0,
            "context": {},
        }
        (tmp_path / "merge-authorized-33333").write_text(json.dumps(token_data))

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
        assert not (tmp_path / "merge-authorized-33333").exists()

    def test_token_expiry_exactly_at_now(self, tmp_path):
        """Token with expires_at exactly equal to now is expired (< now)."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now - 300,
            "expires_at": now,  # Exactly now
            "context": {},
        }
        (tmp_path / "merge-authorized-44444").write_text(json.dumps(token_data))

        # expires_at < now may or may not be true due to time passing between
        # writing and reading; test both paths by mocking
        with patch("merge_guard_pre.time") as mock_time:
            mock_time.time.return_value = now + 0.001  # Just past expiry
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is None

    def test_token_just_before_expiry(self, tmp_path):
        """Token with expires_at just in the future is valid."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 1,  # 1 second left
            "context": {},
        }
        (tmp_path / "merge-authorized-55555").write_text(json.dumps(token_data))

        with patch("merge_guard_pre.time") as mock_time:
            mock_time.time.return_value = now  # Exactly at creation time
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is not None
        assert path is not None

    def test_token_with_empty_json_object(self, tmp_path):
        """Token that is just {} (no fields) is cleaned up."""
        from merge_guard_pre import find_valid_token

        (tmp_path / "merge-authorized-66666").write_text("{}")

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
        # expires_at defaults to 0, which triggers cleanup
        assert not (tmp_path / "merge-authorized-66666").exists()

    def test_token_with_list_json(self, tmp_path):
        """Token that is a JSON list instead of object is cleaned up."""
        from merge_guard_pre import find_valid_token

        (tmp_path / "merge-authorized-77777").write_text("[1, 2, 3]")

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
        # list.get() raises AttributeError → caught by except clause
        assert not (tmp_path / "merge-authorized-77777").exists()

    def test_token_with_boolean_expiry(self, tmp_path):
        """Token with expires_at as boolean is handled."""
        from merge_guard_pre import find_valid_token

        # In Python, bool is a subclass of int: isinstance(True, int) == True
        # True == 1, so expires_at=True is valid (1 second since epoch = expired)
        token_data = {
            "created_at": time.time(),
            "expires_at": True,  # == 1, which is < now
            "context": {},
        }
        (tmp_path / "merge-authorized-88888").write_text(json.dumps(token_data))

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None  # 1 second since epoch is expired

    def test_empty_token_file(self, tmp_path):
        """Empty token file is cleaned up."""
        from merge_guard_pre import find_valid_token

        (tmp_path / "merge-authorized-99999").write_text("")

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
        assert not (tmp_path / "merge-authorized-99999").exists()

    def test_write_token_returns_none_on_readonly_dir(self, tmp_path):
        """write_token returns None when directory is not writable."""
        from merge_guard_post import write_token

        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        os.chmod(str(readonly_dir), 0o444)

        try:
            result = write_token({}, token_dir=readonly_dir)
            assert result is None
        finally:
            os.chmod(str(readonly_dir), 0o755)

    def test_write_token_collision_uses_fallback(self, tmp_path):
        """When first filename exists, fallback with microsecond suffix is used."""
        from merge_guard_post import write_token

        # Pre-create the file that write_token will try to create
        now = time.time()
        timestamp = int(now)
        preexisting = tmp_path / f"merge-authorized-{timestamp}"
        preexisting.write_text("taken")

        with patch("merge_guard_post.time") as mock_time:
            mock_time.time.return_value = now
            result = write_token({"test": True}, token_dir=tmp_path)

        # Must succeed with fallback name (not None)
        assert result is not None
        assert Path(result).exists()
        assert Path(result) != preexisting


# =============================================================================
# Edge cases: main() entry points
# =============================================================================


class TestPostMainEdgeCases:
    """Edge cases for merge_guard_post.main()."""

    def test_tool_output_empty_dict(self, tmp_path):
        """tool_output as empty dict — no token created."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_output": {},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_tool_output_with_answers_key(self, tmp_path):
        """tool_output dict with 'answers' key (actual AskUserQuestion format)."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Merge PR #10?"}]},
            "tool_output": {"answers": {"Merge PR #10?": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 1

    def test_tool_input_missing_questions(self, tmp_path):
        """tool_input without 'questions' key — no token created."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {},
            "tool_output": {"answers": {"anything": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_tool_output_none(self, tmp_path):
        """tool_output as None — no crash, no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Merge?"}]},
            "tool_output": None,
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_tool_output_integer(self, tmp_path):
        """tool_output as integer — converted to string, not affirmative."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Merge?"}]},
            "tool_output": 42,
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_empty_stdin(self, tmp_path):
        """Empty stdin — exits 0 without error."""
        from merge_guard_post import main

        with patch("sys.stdin", io.StringIO("")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_missing_tool_input_key(self, tmp_path):
        """JSON without tool_input key — no crash."""
        from merge_guard_post import main

        input_data = json.dumps({"some_other_key": "value"})

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0


class TestPreMainEdgeCases:
    """Edge cases for merge_guard_pre.main()."""

    def test_missing_tool_input_key(self):
        """JSON without tool_input key — exits 0."""
        from merge_guard_pre import main

        input_data = json.dumps({"other_key": "value"})

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_tool_input_as_string(self):
        """tool_input as a string instead of dict — fails closed (exit 2)."""
        from merge_guard_pre import main

        input_data = json.dumps({"tool_input": "not a dict"})

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        # str.get() raises AttributeError → caught by outer except → fail closed
        assert exc_info.value.code == 2

    def test_empty_stdin(self):
        """Empty stdin — exits 0."""
        from merge_guard_pre import main

        with patch("sys.stdin", io.StringIO("")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_deny_output_format(self, tmp_path, capsys):
        """Verify the exact JSON structure of a deny response."""
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": "git push --force origin main"}
        })

        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Validate complete structure
        assert "hookSpecificOutput" in output
        hook_output = output["hookSpecificOutput"]
        assert hook_output["permissionDecision"] == "deny"
        assert isinstance(hook_output["permissionDecisionReason"], str)
        assert len(hook_output["permissionDecisionReason"]) > 0


# =============================================================================
# Security tests
# =============================================================================


class TestTokenSecurity:
    """Security-focused tests for the merge guard token mechanism."""

    def test_token_file_not_world_readable(self, tmp_path):
        """Token file must be 0o600 — not readable by others."""
        from merge_guard_post import write_token

        result = write_token({"sensitive": True}, token_dir=tmp_path)
        assert result is not None

        mode = os.stat(result).st_mode
        # No group or other read/write/execute bits
        assert mode & stat.S_IRGRP == 0
        assert mode & stat.S_IWGRP == 0
        assert mode & stat.S_IXGRP == 0
        assert mode & stat.S_IROTH == 0
        assert mode & stat.S_IWOTH == 0
        assert mode & stat.S_IXOTH == 0

    def test_token_content_is_valid_json(self, tmp_path):
        """Token file content must be parseable JSON with expected fields."""
        from merge_guard_post import write_token

        result = write_token({"pr": "42"}, token_dir=tmp_path)
        with open(result) as f:
            data = json.load(f)

        assert isinstance(data["created_at"], (int, float))
        assert isinstance(data["expires_at"], (int, float))
        assert isinstance(data["context"], dict)
        assert data["expires_at"] > data["created_at"]

    def test_token_ttl_is_5_minutes(self):
        """TOKEN_TTL constant must be 300 seconds (5 minutes)."""
        from merge_guard_post import TOKEN_TTL as post_ttl
        from merge_guard_pre import TOKEN_TTL as pre_ttl

        assert post_ttl == 300
        assert pre_ttl == 300

    def test_token_ttl_matches_between_hooks(self):
        """Both hooks must agree on TOKEN_TTL."""
        from merge_guard_post import TOKEN_TTL as post_ttl
        from merge_guard_pre import TOKEN_TTL as pre_ttl

        assert post_ttl == pre_ttl

    def test_safe_remove_ignores_missing_file(self, tmp_path):
        """_safe_remove doesn't raise for nonexistent files."""
        from merge_guard_pre import _safe_remove

        # Should not raise
        _safe_remove(str(tmp_path / "nonexistent"))

    def test_large_context_doesnt_crash(self, tmp_path):
        """Token with very large context data still works."""
        from merge_guard_post import write_token
        from merge_guard_pre import find_valid_token

        large_context = {"data": "x" * 10000}
        result = write_token(large_context, token_dir=tmp_path)
        assert result is not None

        token, path = find_valid_token(token_dir=tmp_path)
        assert token is not None
        assert path is not None
        assert len(token["context"]["data"]) == 10000

    def test_post_hook_never_blocks(self, tmp_path):
        """Post hook always exits 0, even on internal errors."""
        from merge_guard_post import main

        # Force an error by making TOKEN_DIR a file instead of directory
        bad_dir = tmp_path / "not_a_dir"
        bad_dir.write_text("I am a file")

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Merge PR #1?"}]},
            "tool_output": {"answers": {"Merge PR #1?": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", bad_dir), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        # Observer hook must NEVER block, even on errors
        assert exc_info.value.code == 0

    def test_pre_hook_fails_closed_on_internal_error(self, tmp_path, capsys):
        """Pre hook exits 2 (deny) on unexpected internal errors — fail closed."""
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": "gh pr merge 42"}
        })

        # Force an error in find_valid_token by making TOKEN_DIR unreadable
        unreadable_dir = tmp_path / "unreadable"
        unreadable_dir.mkdir()

        with patch("merge_guard_pre.TOKEN_DIR", unreadable_dir), \
             patch("merge_guard_pre.glob.glob", side_effect=PermissionError("denied")), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        # Security guard fails closed — deny on internal errors
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "internal error" in output["hookSpecificOutput"]["permissionDecisionReason"].lower()


# =============================================================================
# hooks.json registration tests
# =============================================================================


HOOKS_DIR = Path(__file__).parent.parent / "hooks"
HOOKS_JSON = HOOKS_DIR / "hooks.json"


class TestMergeGuardHooksRegistration:
    """Verify merge guard hooks are correctly registered in hooks.json."""

    @pytest.fixture
    def hooks_config(self):
        content = HOOKS_JSON.read_text(encoding="utf-8")
        return json.loads(content)

    def test_merge_guard_pre_in_pretooluse_bash(self, hooks_config):
        """merge_guard_pre.py must be registered under PreToolUse Bash."""
        entries = hooks_config["hooks"].get("PreToolUse", [])
        bash_hooks = []
        for entry in entries:
            if entry.get("matcher") == "Bash":
                for hook in entry.get("hooks", []):
                    bash_hooks.append(hook.get("command", ""))

        assert any("merge_guard_pre.py" in cmd for cmd in bash_hooks), (
            "merge_guard_pre.py not found in PreToolUse Bash hooks"
        )

    def test_merge_guard_post_in_posttooluse_askuserquestion(self, hooks_config):
        """merge_guard_post.py must be registered under PostToolUse AskUserQuestion."""
        entries = hooks_config["hooks"].get("PostToolUse", [])
        ask_hooks = []
        for entry in entries:
            if entry.get("matcher") == "AskUserQuestion":
                for hook in entry.get("hooks", []):
                    ask_hooks.append(hook.get("command", ""))

        assert any("merge_guard_post.py" in cmd for cmd in ask_hooks), (
            "merge_guard_post.py not found in PostToolUse AskUserQuestion hooks"
        )

    def test_merge_guard_pre_is_synchronous(self, hooks_config):
        """merge_guard_pre.py must be synchronous (blocking) — it affects permissions."""
        entries = hooks_config["hooks"].get("PreToolUse", [])
        for entry in entries:
            if entry.get("matcher") == "Bash":
                for hook in entry.get("hooks", []):
                    if "merge_guard_pre.py" in hook.get("command", ""):
                        assert hook.get("async", False) is not True, (
                            "merge_guard_pre.py must be synchronous — "
                            "it makes permission decisions"
                        )

    def test_merge_guard_post_is_synchronous(self, hooks_config):
        """merge_guard_post.py must be synchronous — token must be written before next tool."""
        entries = hooks_config["hooks"].get("PostToolUse", [])
        for entry in entries:
            if entry.get("matcher") == "AskUserQuestion":
                for hook in entry.get("hooks", []):
                    if "merge_guard_post.py" in hook.get("command", ""):
                        assert hook.get("async", False) is not True, (
                            "merge_guard_post.py must be synchronous — "
                            "token must exist before next tool call"
                        )

    def test_merge_guard_pre_script_exists(self):
        """merge_guard_pre.py script file must exist."""
        assert (HOOKS_DIR / "merge_guard_pre.py").exists()

    def test_merge_guard_post_script_exists(self):
        """merge_guard_post.py script file must exist."""
        assert (HOOKS_DIR / "merge_guard_post.py").exists()

    def test_bash_matcher_has_both_guard_hooks(self, hooks_config):
        """PreToolUse Bash should have both git_commit_check.py and merge_guard_pre.py."""
        entries = hooks_config["hooks"].get("PreToolUse", [])
        bash_commands = []
        for entry in entries:
            if entry.get("matcher") == "Bash":
                for hook in entry.get("hooks", []):
                    bash_commands.append(hook.get("command", ""))

        assert any("git_commit_check.py" in cmd for cmd in bash_commands), (
            "git_commit_check.py missing from PreToolUse Bash"
        )
        assert any("merge_guard_pre.py" in cmd for cmd in bash_commands), (
            "merge_guard_pre.py missing from PreToolUse Bash"
        )


# =============================================================================
# API bypass pattern detection
# =============================================================================


class TestAPIBypassPatterns:
    """Tests for API-based merge bypass detection (gh api, curl, direct push)."""

    def test_gh_api_merge(self):
        """gh api with merge endpoint is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/pulls/42/merge -X PUT"
        )

    def test_gh_api_merge_case_insensitive(self):
        """gh api merge detection is case-insensitive."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/pulls/42/MERGE -X PUT"
        )

    def test_curl_api_merge(self):
        """curl to GitHub merge API is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl -X PUT https://api.github.com/repos/owner/repo/pulls/42/merge'
        )

    def test_curl_api_merge_case_insensitive(self):
        """curl merge detection is case-insensitive."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl -X PUT https://api.github.com/repos/owner/repo/pulls/42/Merge'
        )

    def test_git_push_head_main(self):
        """git push origin HEAD:main bypasses PR merge — dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push origin HEAD:main")

    def test_git_push_head_master(self):
        """git push origin HEAD:master bypasses PR merge — dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push origin HEAD:master")

    def test_git_push_head_main_with_c_flag(self):
        """git -c ... push origin HEAD:main with config flag is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "git -c push.default=current push origin HEAD:main"
        )

    def test_git_push_head_feature_branch_is_safe(self):
        """git push origin HEAD:feature-branch is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("git push origin HEAD:feature/my-branch")

    def test_gh_api_merge_with_method_flag(self):
        """gh api with --method PUT and merge endpoint is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/pulls/42/merge --method PUT"
        )

    def test_gh_api_merge_with_post_method(self):
        """gh api with -X POST and merge endpoint is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X POST repos/owner/repo/pulls/42/merge"
        )

    def test_gh_api_merge_with_patch_method(self):
        """gh api with --method PATCH and merge endpoint is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/pulls/42/merge --method PATCH"
        )

    def test_gh_api_merge_readonly_is_safe(self):
        """gh api with merge in URL but no mutating method is safe (GET)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/pulls/42/merge"
        )

    def test_gh_api_merge_explicit_get_is_safe(self):
        """gh api with -X GET and merge in URL is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/pulls/42/merge -X GET"
        )

    def test_gh_api_mergeable_query_is_safe(self):
        """gh api querying mergeable status is safe (read-only)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/pulls --jq .mergeable"
        )

    def test_gh_api_merge_workflow_is_safe(self):
        """gh api referencing workflow with 'merge' in name is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/actions/workflows/merge-check.yml"
        )

    def test_gh_api_without_merge_is_safe(self):
        """gh api without merge keyword is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh api repos/owner/repo/pulls/42")

    def test_curl_merge_readonly_is_safe(self):
        """curl to merge API without mutating method is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl https://api.github.com/repos/owner/repo/pulls/42/merge"
        )

    def test_curl_merge_explicit_get_is_safe(self):
        """curl -X GET to merge API is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl -X GET https://api.github.com/repos/owner/repo/pulls/42/merge"
        )

    def test_curl_without_merge_is_safe(self):
        """curl without merge keyword is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl https://api.github.com/repos/owner/repo/pulls/42"
        )


# =============================================================================
# Direct push to main/master detection
# =============================================================================


class TestDirectPushToDefaultBranch:
    """Tests for detecting regular pushes to main/master branches."""

    def test_git_push_origin_main(self):
        """git push origin main is dangerous — bypasses PR workflow."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push origin main")

    def test_git_push_origin_master(self):
        """git push origin master is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push origin master")

    def test_git_push_u_origin_main(self):
        """git push -u origin main with tracking flag is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push -u origin main")

    def test_git_push_origin_feature_branch_is_safe(self):
        """git push origin feature-branch is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("git push origin feature/my-branch")

    def test_git_push_origin_main_with_c_flag(self):
        """git -c ... push origin main with config flag is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "git -c push.default=current push origin main"
        )

    def test_git_push_upstream_main(self):
        """git push upstream main (different remote name) is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push upstream main")

    def test_git_push_origin_main_with_valid_token(self, tmp_path):
        """git push origin main is allowed with a valid authorization token."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"op": "push-main"}, token_dir=tmp_path)

        result = check_merge_authorization("git push origin main", token_dir=tmp_path)
        assert result is None  # Allowed

    def test_git_push_origin_main_blocked_without_token(self, tmp_path):
        """git push origin main is blocked without a token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("git push origin main", token_dir=tmp_path)
        assert result is not None
        assert "AskUserQuestion" in result

    def test_git_push_set_upstream_origin_main(self):
        """git push --set-upstream origin main is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push --set-upstream origin main")

    def test_git_push_origin_main_colon_refspec_safe(self):
        """git push origin main:feature is safe — pushes local main to remote feature."""
        from merge_guard_pre import is_dangerous_command

        # 'main:feature' is a single token; 'main' is not at word boundary end
        assert not is_dangerous_command("git push origin main:feature-branch")


# =============================================================================
# Operation scoping (_token_matches_command)
# =============================================================================


class TestOperationScoping:
    """Tests for _token_matches_command — operation scoping validation."""

    def test_token_with_matching_pr_number(self):
        """Token with PR context matches command with same PR number."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"pr_number": "42"}}
        assert _token_matches_command(token, "gh pr merge 42")

    def test_token_with_mismatched_pr_number(self):
        """Token with PR context does NOT match different PR number."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"pr_number": "42"}}
        assert not _token_matches_command(token, "gh pr merge 99")

    def test_token_with_matching_branch(self):
        """Token with branch context matches branch -D command."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"branch": "old-feature"}}
        assert _token_matches_command(token, "git branch -D old-feature")

    def test_token_with_mismatched_branch(self):
        """Token with branch context does NOT match different branch."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"branch": "old-feature"}}
        assert not _token_matches_command(token, "git branch -D other-branch")

    def test_token_with_branch_delete_force(self):
        """Token with branch context matches --delete --force command."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"branch": "cleanup"}}
        assert _token_matches_command(token, "git branch --delete --force cleanup")

    def test_token_without_context_allows_any(self):
        """Token without context allows any command (no scoping)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {}}
        assert _token_matches_command(token, "gh pr merge 42")
        assert _token_matches_command(token, "git branch -D anything")

    def test_token_with_malformed_context(self):
        """Token with non-dict context allows through (graceful degradation)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": "not a dict"}
        assert _token_matches_command(token, "gh pr merge 42")

    def test_token_without_context_key(self):
        """Token missing context key allows through."""
        from merge_guard_pre import _token_matches_command

        token = {}
        assert _token_matches_command(token, "gh pr merge 42")

    def test_pr_context_with_non_pr_command(self):
        """Token has PR context but command is force push — allows through."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"pr_number": "42"}}
        assert _token_matches_command(token, "git push --force origin main")

    def test_branch_context_with_non_branch_command(self):
        """Token has branch context but command is gh pr merge — allows through."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"branch": "old"}}
        assert _token_matches_command(token, "gh pr merge 42")

    def test_mismatched_token_blocks_in_check_merge_authorization(self, tmp_path):
        """check_merge_authorization blocks when token context doesn't match."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        # Token scoped to PR #42
        write_token({"question_snippet": "Merge #42?", "pr_number": "42"},
                    token_dir=tmp_path)

        # Attempt to merge PR #99 — should be blocked
        result = check_merge_authorization("gh pr merge 99", token_dir=tmp_path)
        assert result is not None
        assert "does not match" in result

        # Token should NOT be consumed (it belongs to PR #42)
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1


# =============================================================================
# Session scoping (CLAUDE_SESSION_ID)
# =============================================================================


class TestSessionScoping:
    """Tests for session-scoped token isolation."""

    def test_token_includes_session_id(self, tmp_path):
        """Token file includes session_id when env var is set."""
        from merge_guard_post import write_token

        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "session-abc"}):
            result = write_token({"test": True}, token_dir=tmp_path)

        with open(result) as f:
            data = json.load(f)
        assert data["session_id"] == "session-abc"

    def test_token_omits_session_id_when_not_set(self, tmp_path):
        """Token file omits session_id when env var is not set."""
        from merge_guard_post import write_token

        with patch.dict(os.environ, {}, clear=True):
            # Ensure CLAUDE_SESSION_ID is not in environment
            os.environ.pop("CLAUDE_SESSION_ID", None)
            result = write_token({"test": True}, token_dir=tmp_path)

        with open(result) as f:
            data = json.load(f)
        assert "session_id" not in data

    def test_same_session_token_accepted(self, tmp_path):
        """Token from same session is accepted."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
            "session_id": "session-abc",
        }
        (tmp_path / "merge-authorized-11111").write_text(json.dumps(token_data))

        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "session-abc"}):
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is not None

    def test_different_session_token_rejected(self, tmp_path):
        """Token from different session is skipped."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
            "session_id": "session-other",
        }
        (tmp_path / "merge-authorized-22222").write_text(json.dumps(token_data))

        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "session-abc"}):
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        # Token NOT cleaned up — it may be valid for its own session
        assert (tmp_path / "merge-authorized-22222").exists()

    def test_no_session_id_accepts_any_token(self, tmp_path):
        """When env has no session ID, any valid token is accepted."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
            "session_id": "session-xyz",
        }
        (tmp_path / "merge-authorized-33333").write_text(json.dumps(token_data))

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CLAUDE_SESSION_ID", None)
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is not None

    def test_token_without_session_accepted_by_any_session(self, tmp_path):
        """Token without session_id is accepted regardless of current session."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
        }
        (tmp_path / "merge-authorized-44444").write_text(json.dumps(token_data))

        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "session-abc"}):
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is not None


# =============================================================================
# Fail-closed behavior
# =============================================================================


class TestFailClosed:
    """Tests for fail-closed behavior in the pre-hook."""

    def test_fail_closed_on_exception_in_main(self, capsys):
        """main() outputs deny JSON and exits 2 on unexpected exception."""
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": "gh pr merge 42"}
        })

        with patch("merge_guard_pre.check_merge_authorization",
                   side_effect=RuntimeError("boom")), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_fail_closed_stderr_includes_error(self, capsys):
        """Fail-closed error is logged to stderr for debugging."""
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": "gh pr merge 42"}
        })

        with patch("merge_guard_pre.check_merge_authorization",
                   side_effect=RuntimeError("test boom")), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "merge_guard_pre" in captured.err
        assert "test boom" in captured.err

    def test_fail_closed_even_if_deny_output_fails(self):
        """If deny JSON output itself fails, exit 2 still happens."""
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": "gh pr merge 42"}
        })

        # Patch json.dumps to fail inside the except handler's try block,
        # which causes the inner except Exception: pass to trigger.
        # The sys.exit(2) still runs afterward.
        original_dumps = json.dumps
        call_count = [0]

        def failing_dumps(*args, **kwargs):
            call_count[0] += 1
            # First call is from json.load succeeding (not dumps).
            # The except handler calls json.dumps(output) — fail that one.
            if call_count[0] >= 1:
                raise TypeError("simulated dumps failure")
            return original_dumps(*args, **kwargs)

        with patch("merge_guard_pre.check_merge_authorization",
                   side_effect=RuntimeError("boom")), \
             patch("merge_guard_pre.json.dumps", side_effect=failing_dumps), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2


# =============================================================================
# Token write inner exception handler
# =============================================================================


class TestTokenWriteExceptionHandling:
    """Tests for write_token's exception handling during file operations."""

    def test_double_collision_returns_none(self, tmp_path):
        """When both primary and fallback filenames exist, returns None."""
        from merge_guard_post import write_token

        now = time.time()
        timestamp = int(now)
        ms_suffix = int(now * 1000) % 1000

        # Pre-create both the primary and fallback files
        primary = tmp_path / f"merge-authorized-{timestamp}"
        fallback = tmp_path / f"merge-authorized-{timestamp}-{ms_suffix}"
        primary.write_text("taken")
        fallback.write_text("taken")

        with patch("merge_guard_post.time") as mock_time:
            mock_time.time.return_value = now
            result = write_token({"test": True}, token_dir=tmp_path)

        assert result is None

    def test_fdopen_failure_cleans_up_primary(self, tmp_path):
        """If fdopen/json.dump fails after os.open, the created file is removed."""
        from merge_guard_post import write_token

        original_fdopen = os.fdopen

        call_count = [0]

        def failing_fdopen(fd, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Close the fd before raising to avoid resource leak
                os.close(fd)
                raise OSError("simulated fdopen failure")
            return original_fdopen(fd, *args, **kwargs)

        with patch("merge_guard_post.os.fdopen", side_effect=failing_fdopen):
            result = write_token({"test": True}, token_dir=tmp_path)

        # Should return None because the primary write failed and the retry
        # path (FileExistsError) is not triggered (different exception type)
        assert result is None

    def test_tool_output_as_boolean_true(self, tmp_path):
        """tool_output as boolean True — converted to 'True', not affirmative."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Merge?"}]},
            "tool_output": True,
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # "True" does not match affirmative patterns
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_tool_output_as_boolean_false(self, tmp_path):
        """tool_output as boolean False — empty string, no token created."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Merge?"}]},
            "tool_output": False,
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0


# =============================================================================
# AskUserQuestion schema extraction edge cases (issue #253)
# =============================================================================


class TestQuestionExtractionEdgeCases:
    """Tests for isinstance guards on the questions array extraction path.

    The fix extracts question from tool_input["questions"][0]["question"] with
    isinstance guards at each level. Every malformed input must result in
    question="" which prevents token creation (fail-closed).
    """

    def test_questions_is_string_not_list(self, tmp_path):
        """questions is a string instead of a list — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": "Should I merge?"},
            "tool_output": {"answers": {"Should I merge?": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_questions_is_integer(self, tmp_path):
        """questions is an integer — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": 42},
            "tool_output": {"answers": {"q": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_questions_is_none(self, tmp_path):
        """questions is None — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": None},
            "tool_output": {"answers": {"q": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_questions_is_empty_list(self, tmp_path):
        """questions is an empty list — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": []},
            "tool_output": {"answers": {"q": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_questions_first_element_is_string(self, tmp_path):
        """questions[0] is a string instead of dict — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": ["Should I merge?"]},
            "tool_output": {"answers": {"Should I merge?": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_questions_first_element_is_int(self, tmp_path):
        """questions[0] is an int instead of dict — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [123]},
            "tool_output": {"answers": {"q": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_questions_first_element_is_none(self, tmp_path):
        """questions[0] is None instead of dict — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [None]},
            "tool_output": {"answers": {"q": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_questions_first_element_is_nested_list(self, tmp_path):
        """questions[0] is a nested list instead of dict — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [["nested", "list"]]},
            "tool_output": {"answers": {"q": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_questions_dict_missing_question_key(self, tmp_path):
        """questions[0] is a dict but has no 'question' key — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"header": "Merge", "options": []}]},
            "tool_output": {"answers": {"q": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0


class TestAnswerExtractionEdgeCases:
    """Tests for isinstance guards on the answers dict extraction path.

    The fix extracts answer from tool_output["answers"] dict using
    next(iter(values())). Every malformed input must result in
    answer="" which prevents token creation (fail-closed).
    """

    def test_answers_is_list_not_dict(self, tmp_path):
        """answers is a list instead of dict — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_output": {"answers": ["yes"]},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_answers_is_string(self, tmp_path):
        """answers is a string instead of dict — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_output": {"answers": "yes"},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_answers_is_integer(self, tmp_path):
        """answers is an integer instead of dict — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_output": {"answers": 1},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_answers_is_none(self, tmp_path):
        """answers is None inside tool_output dict — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_output": {"answers": None},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_answers_is_empty_dict(self, tmp_path):
        """answers is an empty dict — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_output": {"answers": {}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_answers_value_is_integer(self, tmp_path):
        """answers value is an integer — converted to str via str(), not affirmative."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_output": {"answers": {"Should I merge?": 42}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_answers_value_is_boolean_true(self, tmp_path):
        """answers value is boolean True — str(True) = 'True', not affirmative."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_output": {"answers": {"Should I merge?": True}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_answers_value_is_none(self, tmp_path):
        """answers value is None — str(None) = 'None', not affirmative."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_output": {"answers": {"Should I merge?": None}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_formatted_string_rejected_by_anchor_pattern(self, tmp_path):
        """tool_output is the formatted string from AskUserQuestion.

        When tool_output is a string like 'User has answered your questions:
        "Confirm merge?"="yes"', the str() fallback path is taken. Since
        is_affirmative() is ^-anchored, 'User has...' won't match — no token.
        """
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Confirm merge of PR #252?"}]},
            "tool_output": 'User has answered your questions: "Confirm merge of PR #252?"="Yes, merge".',
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # String format starts with "User", not an affirmative word — no token
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0


class TestSchemaFixEndToEnd:
    """End-to-end tests using exact AskUserQuestion schema format.

    These tests verify the complete flow: post-hook creates token from
    correctly-formatted AskUserQuestion data, pre-hook reads and consumes it.
    """

    def test_exact_session_log_format(self, tmp_path):
        """Test with the exact format observed in session logs (issue #253).

        This is the canonical AskUserQuestion format with full question
        structure including header, options, and multiSelect.
        """
        from merge_guard_post import main as post_main
        from merge_guard_pre import main as pre_main

        post_input = json.dumps({
            "tool_input": {
                "questions": [{
                    "question": "Confirm merge of PR #252 to main?",
                    "header": "Merge",
                    "options": [
                        {"label": "Yes, merge", "description": "Merge the PR"},
                        {"label": "Cancel", "description": "Abort the merge"},
                    ],
                    "multiSelect": False,
                }]
            },
            "tool_output": {
                "questions": [{
                    "question": "Confirm merge of PR #252 to main?",
                    "header": "Merge",
                    "options": [
                        {"label": "Yes, merge", "description": "Merge the PR"},
                        {"label": "Cancel", "description": "Abort the merge"},
                    ],
                    "multiSelect": False,
                }],
                "answers": {
                    "Confirm merge of PR #252 to main?": "Yes, merge",
                },
            },
        })

        # Post hook: create token
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(post_input)):
            with pytest.raises(SystemExit) as exc_info:
                post_main()
        assert exc_info.value.code == 0

        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1

        # Verify token content
        token_data = json.loads(tokens[0].read_text())
        assert "created_at" in token_data
        assert "expires_at" in token_data
        assert token_data["expires_at"] > token_data["created_at"]
        assert "context" in token_data
        assert token_data["context"]["pr_number"] == "252"

        # Pre hook: consume token and allow merge
        pre_input = json.dumps({
            "tool_input": {"command": "gh pr merge 252 --squash --delete-branch"}
        })
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(pre_input)):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        assert exc_info.value.code == 0

        # Token should be consumed
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_force_push_approval_flow(self, tmp_path):
        """Full flow for force push approval."""
        from merge_guard_post import main as post_main
        from merge_guard_pre import main as pre_main

        post_input = json.dumps({
            "tool_input": {
                "questions": [{
                    "question": "Force push to origin/main? This will overwrite remote history.",
                }]
            },
            "tool_output": {
                "answers": {
                    "Force push to origin/main? This will overwrite remote history.": "yes",
                },
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(post_input)):
            with pytest.raises(SystemExit):
                post_main()

        assert len(list(tmp_path.glob("merge-authorized-*"))) == 1

        pre_input = json.dumps({
            "tool_input": {"command": "git push --force origin main"}
        })
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(pre_input)):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        assert exc_info.value.code == 0

    def test_branch_delete_approval_flow(self, tmp_path):
        """Full flow for branch deletion approval."""
        from merge_guard_post import main as post_main
        from merge_guard_pre import main as pre_main

        post_input = json.dumps({
            "tool_input": {
                "questions": [{
                    "question": "Delete branch feat/old-feature?",
                }]
            },
            "tool_output": {
                "answers": {
                    "Delete branch feat/old-feature?": "go ahead",
                },
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(post_input)):
            with pytest.raises(SystemExit):
                post_main()

        assert len(list(tmp_path.glob("merge-authorized-*"))) == 1

        pre_input = json.dumps({
            "tool_input": {"command": "git branch -D feat/old-feature"}
        })
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(pre_input)):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        assert exc_info.value.code == 0

    def test_denial_creates_no_token(self, tmp_path):
        """User denies merge — no token, subsequent command blocked."""
        from merge_guard_post import main as post_main
        from merge_guard_pre import main as pre_main

        post_input = json.dumps({
            "tool_input": {
                "questions": [{
                    "question": "Merge PR #100 to main?",
                    "options": [
                        {"label": "Yes, merge"},
                        {"label": "Cancel"},
                    ],
                }]
            },
            "tool_output": {
                "answers": {
                    "Merge PR #100 to main?": "Cancel",
                },
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(post_input)):
            with pytest.raises(SystemExit):
                post_main()

        # No token created for non-affirmative answer
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

        # Pre hook blocks the command
        pre_input = json.dumps({
            "tool_input": {"command": "gh pr merge 100"}
        })
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(pre_input)):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        assert exc_info.value.code == 2  # Blocked

    def test_session_scoped_token_from_schema(self, tmp_path):
        """Token includes session_id when CLAUDE_SESSION_ID is set."""
        from merge_guard_post import main as post_main

        post_input = json.dumps({
            "tool_input": {
                "questions": [{"question": "Should I merge PR #42?"}]
            },
            "tool_output": {
                "answers": {"Should I merge PR #42?": "yes"}
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(post_input)), \
             patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session-123"}):
            with pytest.raises(SystemExit):
                post_main()

        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1
        token_data = json.loads(tokens[0].read_text())
        assert token_data["session_id"] == "test-session-123"

    def test_multi_question_uses_first_only(self, tmp_path):
        """When multiple questions exist, only the first is checked for merge keywords."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {
                "questions": [
                    {"question": "Should I merge PR #42?"},
                    {"question": "Also update the changelog?"},
                ]
            },
            "tool_output": {
                "answers": {
                    "Should I merge PR #42?": "yes",
                    "Also update the changelog?": "yes",
                },
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit):
                main()

        # Token created because first question contains merge keyword
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 1

    def test_multi_question_first_not_merge(self, tmp_path):
        """When first question is not merge-related, no token even if second is."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {
                "questions": [
                    {"question": "Update the changelog?"},
                    {"question": "Then merge PR #42?"},
                ]
            },
            "tool_output": {
                "answers": {
                    "Update the changelog?": "yes",
                    "Then merge PR #42?": "yes",
                },
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit):
                main()

        # No token — first question is not merge-related
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_multi_question_answer_mismatch_no_spurious_token(self, tmp_path):
        """Merge question denied but other question affirmed — no token.

        Regression test: when questions[0] is merge-related but the user
        denied it, and a different question's affirmative answer appears
        first in the answers dict, no token should be created. The fix
        uses answers.get(question) for explicit key lookup instead of
        next(iter(answers.values())).
        """
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {
                "questions": [
                    {"question": "Merge PR #42 to main?"},
                    {"question": "Update the changelog?"},
                ]
            },
            "tool_output": {
                "answers": {
                    "Update the changelog?": "yes",
                    "Merge PR #42 to main?": "no",
                },
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit):
                main()

        # No token — user denied the merge question, even though
        # "yes" from the changelog question appeared first in the dict
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0
