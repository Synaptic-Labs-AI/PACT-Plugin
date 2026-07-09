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

    def test_merge_command_detected_not_bare_keyword(self):
        from merge_guard_post import is_merge_question

        # KD-9: is_merge_question is now a command-driven COARSE HINT — it fires
        # on an embedded destructive COMMAND, not on a bare prose keyword.
        assert is_merge_question("Do you want to run `gh pr merge 5`?")
        assert not is_merge_question("Do you want to merge this PR?")

    def test_force_push_command_detected_not_prose(self):
        from merge_guard_post import is_merge_question

        assert is_merge_question("Should I run `git push --force origin main`?")
        assert not is_merge_question("Should I force push to main?")

    def test_force_push_prose_alone_not_detected(self):
        from merge_guard_post import is_merge_question

        assert not is_merge_question("Should I force-push to main?")

    def test_delete_branch_command_detected_not_prose(self):
        from merge_guard_post import is_merge_question

        assert is_merge_question("Run `git branch -D feat/test`?")
        assert not is_merge_question("Should I delete branch feat/test?")

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

    def test_command_detection_not_bare_uppercase_keyword(self):
        from merge_guard_post import is_merge_question

        # A bare uppercase keyword does NOT fire (command-driven, not keyword);
        # the embedded command does.
        assert not is_merge_question("MERGE this PR now?")
        assert is_merge_question("Run `gh pr merge 5` now?")


# (removed class TestIsAffirmative — merge_guard_post.is_affirmative and its
#  AFFIRMATIVE_PATTERNS regex were removed as production-orphaned dead code:
#  OPTION-mode approval is an exact non-decline label match, never a free-text
#  word allowlist, so the affirmative prefix matcher had no remaining call site.)

# (removed class TestExtractContext — superseded by the command-anchored
#  bidirectional suite in test_merge_guard_auth_symmetry.py;
#  it exercised the dropped prose classifier extract_context.)

class TestWriteToken:
    """Tests for merge_guard_post.write_token().

    Per the sparse-context guard, write_token requires the context dict
    to carry at least one concrete anchor (pr_number OR operation_type).
    Tests that exercise the file-write side use a minimal-but-valid
    context (operation_type alone is sufficient).
    """

    # Minimal context that satisfies the sparse-context guard (one anchor).
    _MIN_CTX = {"operation_type": "merge"}

    def test_creates_token_file(self, tmp_path):
        from merge_guard_post import write_token

        result = write_token({"test": True, **self._MIN_CTX}, token_dir=tmp_path)
        assert result is not None
        assert Path(result).exists()

    def test_token_has_correct_structure(self, tmp_path):
        from merge_guard_post import write_token

        result = write_token({"test": True, **self._MIN_CTX}, token_dir=tmp_path)
        with open(result) as f:
            data = json.load(f)

        assert "created_at" in data
        assert "expires_at" in data
        assert "context" in data
        assert data["context"]["test"] is True

    def test_token_has_correct_ttl(self, tmp_path):
        from merge_guard_post import write_token, TOKEN_TTL

        result = write_token(dict(self._MIN_CTX), token_dir=tmp_path)
        with open(result) as f:
            data = json.load(f)

        assert data["expires_at"] - data["created_at"] == pytest.approx(TOKEN_TTL, abs=1)

    def test_token_file_permissions(self, tmp_path):
        from merge_guard_post import write_token

        result = write_token(dict(self._MIN_CTX), token_dir=tmp_path)
        mode = os.stat(result).st_mode & 0o777
        assert mode == 0o600

    def test_token_filename_prefix(self, tmp_path):
        from merge_guard_post import write_token

        result = write_token(dict(self._MIN_CTX), token_dir=tmp_path)
        assert "merge-authorized-" in Path(result).name

    def test_token_includes_max_uses_field(self, tmp_path):
        """Token JSON carries the max_uses field for N-use authorization (#720 Bug C)."""
        from merge_guard_post import write_token
        from shared.merge_guard_common import MAX_USES

        result = write_token(dict(self._MIN_CTX), token_dir=tmp_path)
        with open(result) as f:
            data = json.load(f)

        assert data["max_uses"] == MAX_USES

    def test_token_includes_uses_remaining_field(self, tmp_path):
        """Token JSON carries uses_remaining = max_uses at write time (#720 Bug C)."""
        from merge_guard_post import write_token
        from shared.merge_guard_common import MAX_USES

        result = write_token(dict(self._MIN_CTX), token_dir=tmp_path)
        with open(result) as f:
            data = json.load(f)

        assert data["uses_remaining"] == MAX_USES


class TestWriteTokenSparseContextGuard:
    """Pin the F-2 sparse-context refusal at the write boundary.

    Refusing to write tokens whose context lacks BOTH pr_number AND
    operation_type prevents wildcard-permissive matches in
    `_token_matches_command`: a `{pr_number: None, operation_type: None}`
    token would otherwise be authorized against ANY destructive command
    via the ladder's "ambiguous-permissive" fallback.
    """

    def test_write_token_rejects_no_pr_number_no_op_type(self, tmp_path, capsys):
        from merge_guard_post import write_token

        result = write_token({}, token_dir=tmp_path)
        assert result is None
        # No file should have been created
        assert list(Path(tmp_path).glob("merge-authorized-*")) == []
        # Stderr warning emitted (forensic visibility)
        captured = capsys.readouterr()
        assert "[security]" in captured.err
        assert "sparse context" in captured.err

    def test_write_token_rejects_only_question_snippet(self, tmp_path, capsys):
        """Realistic shape from extract_context() on vague question text.

        `extract_context("Merge?")` returns `{question_snippet: "Merge?"}`
        with neither pr_number nor operation_type. The guard refuses.
        """
        from merge_guard_post import write_token

        result = write_token({"question_snippet": "Merge?"}, token_dir=tmp_path)
        assert result is None
        captured = capsys.readouterr()
        assert "[security]" in captured.err

    def test_write_token_rejects_pr_number_alone(self, tmp_path, capsys):
        """FAIL-CLOSED (never-mint-op_type=None): a pr_number WITHOUT an
        operation_type is refused — an untyped token can never positively match a
        command on the read side, so it is never written."""
        from merge_guard_post import write_token

        result = write_token({"pr_number": "663"}, token_dir=tmp_path)
        assert result is None
        assert list(Path(tmp_path).glob("merge-authorized-*")) == []
        captured = capsys.readouterr()
        assert "[security]" in captured.err

    def test_write_token_accepts_op_type_alone(self, tmp_path):
        """One concrete anchor is sufficient — operation_type alone allows write."""
        from merge_guard_post import write_token

        result = write_token({"operation_type": "merge"}, token_dir=tmp_path)
        assert result is not None
        assert Path(result).exists()

    def test_write_token_rejects_non_dict_context(self, tmp_path, capsys):
        from merge_guard_post import write_token

        result = write_token("not_a_dict", token_dir=tmp_path)
        assert result is None
        captured = capsys.readouterr()
        assert "[security]" in captured.err
        assert "non-dict" in captured.err


class TestPostMainEntryPoint:
    """Tests for merge_guard_post.main() stdin/exit behavior."""

    def test_main_exits_0_on_merge_approval(self, tmp_path, capsys):
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{
                "question": "Merge the PR?",
                "options": [
                    {"label": "Yes, merge", "description": "Run `gh pr merge 42`"},
                    {"label": "Cancel", "description": "Abort"},
                ],
            }]},
            "tool_response": {"answers": {"Merge the PR?": "Yes, merge"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"suppressOutput": True}
        # Verify token was created
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1

    def test_main_exits_0_on_non_merge_question(self, tmp_path):
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I add logging?"}]},
            "tool_response": {"answers": {"Should I add logging?": "yes"}},
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
            "tool_response": {"answers": {"Should I merge #42?": "no"}},
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

    def test_main_rejects_string_tool_response(self, tmp_path):
        """Non-dict tool_response exits early — no token created."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_response": "yes",
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("merge_guard_post.write_token") as mock_write_token:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # Non-dict tool_response rejected — only dict format trusted
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0
        # Cause-of-no-token: malformed tool_response triggered early-exit
        # before the merge-question branch could reach write_token. Without
        # this, the test would still pass if a silent regression caused
        # tool_response reading to degrade to empty-dict + happy-path skip.
        mock_write_token.assert_not_called()


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

    def test_skips_token_with_all_slots_claimed(self, tmp_path):
        """Slot-claim filter (#720 Bug C): when all use slots are already
        claimed but the .consumed rename was lost to a transient FS error,
        find_valid_token treats the token as already-consumed (skips it
        + race-recovers the rename)."""
        from shared.merge_guard_common import MAX_USES, USE_MARKER_SUFFIX
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_path = tmp_path / "merge-authorized-77777"
        token_path.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"pr_number": "42", "operation_type": "merge"},
            "max_uses": MAX_USES,
            "uses_remaining": 0,
        }))
        # Pre-create all slot markers — simulating the all-claimed state
        # with the terminal rename lost.
        for slot in range(1, MAX_USES + 1):
            (tmp_path / f"merge-authorized-77777{USE_MARKER_SUFFIX}{slot}").write_text(
                json.dumps({"slot": slot, "consumed_at": now})
            )

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
        # Race-recovery: the missed terminal rename was attempted
        assert not token_path.exists()
        assert (tmp_path / "merge-authorized-77777.consumed").exists()

    def test_skips_use_marker_files_as_tokens(self, tmp_path):
        """`.use-N` marker files are not themselves tokens — find_valid_token
        must not attempt to parse them as token JSON."""
        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_pre import find_valid_token

        # An orphan .use-N marker with no parent token
        marker = tmp_path / f"merge-authorized-66666{USE_MARKER_SUFFIX}1"
        marker.write_text(json.dumps({"slot": 1, "consumed_at": time.time()}))

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
        # Marker file was not erroneously cleaned up
        assert marker.exists()


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
            "context": {"operation_type": "merge", "pr_number": "42"},
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

    def test_main_exits_0_on_safe_command(self, capsys):
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": "git status"}
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"suppressOutput": True}

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
        # Issue #658: hookEventName is required by the harness schema; missing
        # it causes silent rejection and the deny fails open.
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

    def test_main_exits_0_on_dangerous_with_valid_token(self, tmp_path, capsys):
        from merge_guard_pre import main

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {"operation_type": "merge", "pr_number": "42"},
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
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"suppressOutput": True}

    def test_main_exits_0_on_invalid_json(self, capsys):
        from merge_guard_pre import main

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"suppressOutput": True}

    def test_main_exits_0_on_empty_command(self, capsys):
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": ""}
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"suppressOutput": True}

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
        """Full flow: user approves merge (command-anchored), then the matching
        dangerous command is allowed."""
        from shared.merge_guard_common import extract_command_context
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        # Simulate post hook: mint a token from the approved command.
        context = extract_command_context("gh pr merge 42")
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

        # Step 1: Post hook processes merge approval (option-anchored #32)
        post_input = json.dumps({
            "tool_input": {"questions": [{
                "question": "Merge the PR?",
                "options": [
                    {"label": "Yes, merge", "description": "Run `gh pr merge 99`"},
                    {"label": "Cancel", "description": "Abort"},
                ],
            }]},
            "tool_response": {"answers": {"Merge the PR?": "Yes, merge"}},
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

    def test_second_write_retires_first_per_I1(self, tmp_path):
        """I-1 enforcement: second write_token atomically retires the first.

        Per invariant I-1 (at most one unused token at any time),
        write_token calls cleanup_unused_tokens() before os.open(O_EXCL),
        atomically renaming any prior unused token to .consumed. The
        aggregate authorization budget is therefore MAX_USES of the
        surviving token only, not 2*MAX_USES across two coexisting tokens.

        # counter-test: revert the cleanup_unused_tokens(token_dir) call
        #               in merge_guard_post.write_token before O_EXCL →
        #               this test goes RED on len(unused) == 1 assertion
        #               (would be 2 unused tokens coexisting).
        # expected RED cardinality: {1}
        """
        from shared.merge_guard_common import MAX_USES
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "merge", "pr_number": "1"}, token_dir=tmp_path)
        # Force a different filename by manipulating time
        with patch("merge_guard_post.time") as mock_time:
            mock_time.time.return_value = time.time() + 1
            write_token({"operation_type": "merge", "pr_number": "1"}, token_dir=tmp_path)

        # I-1: exactly one unused token after the second write; the first
        # has been atomically retired to .consumed by cleanup_unused_tokens.
        all_files = list(tmp_path.glob("merge-authorized-*"))
        unused = [
            t for t in all_files
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        consumed = [t for t in all_files if str(t).endswith(".consumed")]
        assert len(unused) == 1, f"I-1 violated: {len(unused)} unused tokens"
        assert len(consumed) == 1, (
            f"Expected 1 .consumed (the retired first), got {len(consumed)}"
        )

        # Aggregate budget is MAX_USES (of the surviving token), not 2*MAX_USES.
        for _ in range(MAX_USES):
            assert check_merge_authorization("gh pr merge 1", token_dir=tmp_path) is None

        # Next operation (same PR): blocked — the surviving token's budget is
        # exhausted (not a target mismatch).
        result = check_merge_authorization("gh pr merge 1", token_dir=tmp_path)
        assert result is not None

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


class TestNUseBoundedToken:
    """N-use bounded token semantics (#720 Bug C, MAX_USES=2).

    A single AskUserQuestion approval now authorizes up to MAX_USES
    identical-context retries within TOKEN_TTL via per-use slot markers.
    The (MAX_USES + 1)-th identical-context command requires a fresh
    AskUserQuestion approval, preserving the "stop and reconsider"
    checkpoint.
    """

    def test_first_use_succeeds(self, tmp_path):
        """First identical-context use is authorized; slot-1 marker created;
        token still present (terminal rename happens only on last slot)."""
        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)
        assert token_path is not None
        assert Path(token_path).exists()

        # First command: allowed; slot-1 claimed; token not yet .consumed
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is None  # Allowed
        assert Path(token_path).exists()  # Token still on disk (budget remaining)
        assert Path(token_path + USE_MARKER_SUFFIX + "1").exists()
        assert not Path(token_path + ".consumed").exists()

    def test_second_use_within_budget_succeeds(self, tmp_path):
        """Second identical-context use IS authorized under MAX_USES=2.
        After this, BOTH use-1 and use-2 markers exist and the token has
        been terminally renamed to .consumed."""
        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)

        result1 = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result1 is None

        # Second command within MAX_USES budget: allowed
        result2 = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result2 is None
        assert Path(token_path + USE_MARKER_SUFFIX + "1").exists()
        assert Path(token_path + USE_MARKER_SUFFIX + "2").exists()
        # Terminal rename on last slot
        assert not Path(token_path).exists()
        assert Path(token_path + ".consumed").exists()

    def test_third_use_blocked_after_budget_exhausted(self, tmp_path):
        """Third identical-context use is blocked — budget exhausted,
        requires fresh AskUserQuestion approval."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)

        # Burn through both slots
        assert check_merge_authorization("gh pr merge 42", token_dir=tmp_path) is None
        assert check_merge_authorization("gh pr merge 42", token_dir=tmp_path) is None

        # Third: blocked
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "AskUserQuestion" in result

    def test_safe_commands_do_not_consume_token(self, tmp_path):
        """Safe commands don't claim any slot; full budget remains."""
        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)

        result = check_merge_authorization("git status", token_dir=tmp_path)
        assert result is None
        assert Path(token_path).exists()  # Token still present
        # No slot markers created
        assert not Path(token_path + USE_MARKER_SUFFIX + "1").exists()
        assert not Path(token_path + USE_MARKER_SUFFIX + "2").exists()

    def test_consumption_does_not_interfere_with_expired_cleanup(self, tmp_path):
        """Expired tokens are cleaned up normally alongside N-use consumption."""
        from shared.merge_guard_common import USE_MARKER_SUFFIX
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
        valid_path = write_token({"operation_type": "merge", "pr_number": "99"}, token_dir=tmp_path)

        # Authorize: expired cleaned up, valid claims slot 1 (token still
        # present because MAX_USES=2 and only one slot has been used).
        result = check_merge_authorization("gh pr merge 99", token_dir=tmp_path)
        assert result is None
        assert not expired_file.exists()  # Expired: cleaned up
        assert Path(valid_path).exists()  # Valid: still present (slot 1 claimed)
        assert Path(valid_path + USE_MARKER_SUFFIX + "1").exists()

    def test_each_approval_authorizes_max_uses_operations(self, tmp_path):
        """Each approval authorizes exactly MAX_USES operations."""
        from shared.merge_guard_common import MAX_USES
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        # First approval: authorizes MAX_USES merges
        write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)
        for _ in range(MAX_USES):
            assert check_merge_authorization("gh pr merge 42", token_dir=tmp_path) is None

        # Blocked: budget exhausted (same PR)
        assert check_merge_authorization("gh pr merge 42", token_dir=tmp_path) is not None

        # Second approval (different op): authorizes MAX_USES force-pushes
        with patch("merge_guard_post.time") as mock_time:
            mock_time.time.return_value = time.time() + 1
            write_token({"operation_type": "force-push", "target_ref": "main"}, token_dir=tmp_path)
        for _ in range(MAX_USES):
            assert check_merge_authorization(
                "git push --force origin main", token_dir=tmp_path
            ) is None

        # Blocked again (cross-op + budget)
        assert check_merge_authorization("git branch -D old", token_dir=tmp_path) is not None

    def test_concurrent_deletion_is_safe(self, tmp_path):
        """If token is externally deleted before any consume, command is blocked."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)

        # Simulate external deletion before any consume happens.
        os.unlink(token_path)

        # Command should be blocked because token no longer exists.
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None

    def test_concurrent_consumption_claims_distinct_slots(self, tmp_path):
        """Two concurrent consumes claim distinct slots (slot 1 + slot 2),
        not the same slot. Verifies O_EXCL race semantics."""
        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)

        # Two back-to-back consumes simulate concurrent invocations
        # racing on the same token.
        assert _consume_token(token_path) is True
        assert _consume_token(token_path) is True

        # BOTH slot markers exist (distinct slots claimed)
        assert Path(token_path + USE_MARKER_SUFFIX + "1").exists()
        assert Path(token_path + USE_MARKER_SUFFIX + "2").exists()

        # Token was terminally renamed when slot 2 was claimed
        assert not Path(token_path).exists()
        assert Path(token_path + ".consumed").exists()

        # A third consume attempt fails (budget exhausted, no slot to claim)
        assert _consume_token(token_path) is False


class TestNUseBackwardCompat:
    """Legacy tokens missing the max_uses field are treated as N=1 (#720 Bug C).

    Tokens written by pre-#720 merge_guard_post lack max_uses /
    uses_remaining fields. _consume_token defaults them to single-use
    semantics — the first consume renames directly to .consumed (no slot
    markers), preserving prior behavior for in-flight legacy tokens.
    """

    def _write_legacy_token(self, tmp_path) -> str:
        """Write a token in the pre-#720 schema (no max_uses field)."""
        now = time.time()
        token_path = tmp_path / "merge-authorized-99999"
        token_path.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"pr_number": "42", "operation_type": "merge"},
        }))
        return str(token_path)

    def test_legacy_token_first_use_renames_directly(self, tmp_path):
        """First consume of a legacy token renames it to .consumed (no slot markers)."""
        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_pre import _consume_token

        token_path = self._write_legacy_token(tmp_path)
        assert _consume_token(token_path) is True

        # Terminal rename happened on first consume (legacy single-use)
        assert not Path(token_path).exists()
        assert Path(token_path + ".consumed").exists()
        # No slot markers created
        assert not Path(token_path + USE_MARKER_SUFFIX + "1").exists()

    def test_legacy_token_second_use_blocked(self, tmp_path):
        """Second consume of a legacy token is the idempotent-recognize path
        (preserves the prior FileNotFoundError → .consumed-exists semantics)."""
        from merge_guard_pre import _consume_token

        token_path = self._write_legacy_token(tmp_path)
        assert _consume_token(token_path) is True

        # Second call: original gone, .consumed present → idempotent True
        # (this preserves pre-#720 behavior; a legacy token has no slot
        # markers so the n-use exhausted path is not triggered).
        assert _consume_token(token_path) is True

    def test_legacy_token_blocks_subsequent_authorization(self, tmp_path):
        """End-to-end: a legacy token authorizes ONE command, then blocks."""
        from merge_guard_pre import check_merge_authorization

        token_path = self._write_legacy_token(tmp_path)

        # First command authorized
        assert check_merge_authorization("gh pr merge 42", token_dir=tmp_path) is None
        assert Path(token_path + ".consumed").exists()

        # Second command blocked — no active token remains
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "AskUserQuestion" in result


class TestNUseAuditEmit:
    """Per-consume stderr audit emit (#720 Bug C).

    Each successful slot claim emits a [security] line to stderr in the
    format `[security] merge-authorized token consumed (slot N/MAX):
    <basename>`. Format is invariant under MAX_USES changes — `slot N/MAX`
    reads correctly regardless of the current MAX_USES value.
    """

    def test_audit_emitted_for_each_slot(self, tmp_path, capfd):
        """Each consume emits exactly one [security] line."""
        from shared.merge_guard_common import MAX_USES
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)

        # Drain prior stderr (write_token also emits)
        capfd.readouterr()

        for slot in range(1, MAX_USES + 1):
            assert _consume_token(token_path) is True
            captured = capfd.readouterr()
            assert (
                f"[security] merge-authorized token consumed "
                f"(slot {slot}/{MAX_USES})" in captured.err
            )
            assert os.path.basename(token_path) in captured.err

    def test_audit_format_is_max_uses_invariant(self, tmp_path, capfd):
        """The 'slot N/MAX' format substitutes the configured MAX_USES,
        so future changes to MAX_USES don't break the parseable form."""
        from shared.merge_guard_common import MAX_USES
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)
        capfd.readouterr()

        _consume_token(token_path)
        captured = capfd.readouterr()
        # The exact MAX_USES integer appears in the denominator
        assert f"/{MAX_USES})" in captured.err

    def test_no_audit_emit_when_slot_unclaimable(self, tmp_path, capfd):
        """When every slot is already claimed, _consume_token returns False
        and emits no [security] consume line for that invocation."""
        from shared.merge_guard_common import MAX_USES
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)
        for _ in range(MAX_USES):
            _consume_token(token_path)

        capfd.readouterr()  # Drain prior emits
        assert _consume_token(token_path) is False
        captured = capfd.readouterr()
        assert "[security] merge-authorized token consumed" not in captured.err


class TestNUseSlotMarkerCleanup:
    """`.use-N` markers are cleaned up alongside `.consumed` files (#720 Bug C).

    The cleanup_consumed_tokens helper now reaps both `.consumed` files
    and `.use-N` markers older than TOKEN_TTL.
    """

    def test_stale_use_markers_cleaned_up(self, tmp_path):
        """`.use-N` markers older than TOKEN_TTL are removed by cleanup."""
        from shared.merge_guard_common import (
            MAX_USES,
            TOKEN_TTL,
            USE_MARKER_SUFFIX,
            cleanup_consumed_tokens,
        )
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)
        for _ in range(MAX_USES):
            _consume_token(token_path)

        # Slot markers exist
        for slot in range(1, MAX_USES + 1):
            assert Path(token_path + USE_MARKER_SUFFIX + str(slot)).exists()

        # Age them past TTL
        old_mtime = time.time() - (2 * TOKEN_TTL)
        for marker in tmp_path.glob("merge-authorized-*.use-*"):
            os.utime(marker, (old_mtime, old_mtime))
        # Age the .consumed file too so it doesn't interfere
        os.utime(token_path + ".consumed", (old_mtime, old_mtime))

        cleanup_consumed_tokens(tmp_path)

        # All slot markers reaped
        remaining = list(tmp_path.glob("merge-authorized-*.use-*"))
        assert remaining == []

    def test_fresh_use_markers_retained(self, tmp_path):
        """`.use-N` markers within TTL are NOT cleaned up."""
        from shared.merge_guard_common import (
            MAX_USES,
            USE_MARKER_SUFFIX,
            cleanup_consumed_tokens,
        )
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)
        for _ in range(MAX_USES):
            _consume_token(token_path)

        # Markers are fresh — cleanup should not reap them
        cleanup_consumed_tokens(tmp_path)
        for slot in range(1, MAX_USES + 1):
            assert Path(token_path + USE_MARKER_SUFFIX + str(slot)).exists()

    def test_reaper_boundary_tracks_token_ttl(self, tmp_path):
        """Guard: the reaper's staleness boundary IS TOKEN_TTL, not a hardcoded literal.

        Regression-class guard for the reaper-path-vs-auth-path cert-gap. The reaper
        (cleanup_consumed_tokens) reaps by ``now - mtime > TOKEN_TTL``, so any TTL
        change silently shifts the boundary: reaper-path *mtime* tests that hardcode
        an offset (the original ``- 600``) break at the new TTL, while auth-path
        *stored-expires_at* fixtures do not. This pins the boundary to TOKEN_TTL by
        aging two ``.consumed`` files that straddle it — both offsets derived from
        TOKEN_TTL so the test self-adjusts at any TTL. If the reaper threshold is ever
        replaced with a hardcoded constant, one arm flips and this fails. (The same
        ``now - mtime > TOKEN_TTL`` predicate governs the ``.use-N`` markers, so
        pinning one pattern pins the shared boundary.)
        """
        from shared.merge_guard_common import TOKEN_TTL, cleanup_consumed_tokens

        # Proportional margin: self-adjusts with TOKEN_TTL and stays comfortably
        # larger than test-execution wall-clock drift for any realistic TTL.
        margin = TOKEN_TTL // 10
        now = time.time()

        within = tmp_path / "merge-authorized-00001.consumed"  # younger than TOKEN_TTL
        within.write_text('{}')
        os.utime(within, (now - (TOKEN_TTL - margin), now - (TOKEN_TTL - margin)))

        beyond = tmp_path / "merge-authorized-00002.consumed"  # older than TOKEN_TTL
        beyond.write_text('{}')
        os.utime(beyond, (now - (TOKEN_TTL + margin), now - (TOKEN_TTL + margin)))

        cleanup_consumed_tokens(tmp_path)

        assert within.exists(), (
            "a .consumed file younger than TOKEN_TTL must be RETAINED — the reaper "
            "boundary must track TOKEN_TTL, not a hardcoded literal"
        )
        assert not beyond.exists(), (
            "a .consumed file older than TOKEN_TTL must be REAPED — the reaper "
            "boundary must track TOKEN_TTL, not a hardcoded literal"
        )


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

    def test_multiline_command_with_backslash_detected(self):
        """Backslash-continuation across lines IS detected after normalization.

        Line continuations (\\<newline>) are normalized to spaces before pattern
        matching, closing a bypass vector.
        """
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push \\\n--force origin main")

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
# False-positive prevention: _strip_non_executable_content()
# =============================================================================


class TestStripNonExecutableContent:
    """Tests for _strip_non_executable_content() helper."""

    def test_strips_echo_double_quoted(self):
        """echo with double-quoted dangerous text is stripped."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content('echo "gh pr merge 255"')
        assert "gh pr merge" not in result

    def test_strips_echo_single_quoted(self):
        """echo with single-quoted dangerous text is stripped."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content("echo 'gh pr merge 255'")
        assert "gh pr merge" not in result

    def test_strips_printf_quoted(self):
        """printf with quoted dangerous text is stripped."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content('printf "gh pr merge %d" 42')
        assert "gh pr merge" not in result

    def test_strips_echo_with_flags(self):
        """echo -n with quoted dangerous text is stripped."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content('echo -n "gh pr merge 42"')
        assert "gh pr merge" not in result

    def test_strips_variable_assignment_double_quoted(self):
        """Variable assignment with double-quoted value is stripped."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content('CMD="gh pr merge 42"')
        assert "gh pr merge" not in result

    def test_strips_variable_assignment_single_quoted(self):
        """Variable assignment with single-quoted value is stripped."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content("CMD='gh pr merge 42'")
        assert "gh pr merge" not in result

    def test_strips_comment_at_line_start(self):
        """Comment at start of line is stripped."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content("# gh pr merge 42")
        assert "gh pr merge" not in result

    def test_strips_comment_after_command(self):
        """Comment after a command is stripped."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content("git status # gh pr merge 42")
        assert "gh pr merge" not in result
        assert "git status" in result

    def test_strips_comment_after_semicolon(self):
        """Comment after semicolon is stripped."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content("echo done;# gh pr merge")
        assert "gh pr merge" not in result

    def test_strips_heredoc_body(self):
        """Heredoc body content is stripped."""
        from merge_guard_pre import _strip_non_executable_content

        cmd = "python3 << 'EOF'\nre.compile(r'gh pr merge')\nEOF"
        result = _strip_non_executable_content(cmd)
        assert "gh pr merge" not in result

    def test_strips_heredoc_unquoted_marker(self):
        """Heredoc with unquoted marker is stripped."""
        from merge_guard_pre import _strip_non_executable_content

        cmd = "cat << EOF\ngh pr merge 42\nEOF"
        result = _strip_non_executable_content(cmd)
        assert "gh pr merge" not in result

    def test_strips_heredoc_double_quoted_marker(self):
        """Heredoc with double-quoted marker is stripped."""
        from merge_guard_pre import _strip_non_executable_content

        cmd = 'cat << "PYEOF"\ngh pr merge 42\nPYEOF'
        result = _strip_non_executable_content(cmd)
        assert "gh pr merge" not in result

    def test_preserves_bash_c_single_quoted(self):
        """bash -c 'dangerous' is NOT stripped — it's executable."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content("bash -c 'gh pr merge 42'")
        assert "gh pr merge" in result

    def test_preserves_bare_dangerous_command(self):
        """Bare dangerous commands are preserved."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content("gh pr merge 42")
        assert "gh pr merge" in result

    def test_preserves_chained_dangerous_command(self):
        """Dangerous commands after && are preserved."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content("cd /tmp && gh pr merge 42")
        assert "gh pr merge" in result

    def test_variable_assignment_escaped_quotes(self):
        """Variable assignment with escaped quotes inside is stripped."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content(r'X="gh pr merge \"42\""')
        assert "gh pr merge" not in result

    def test_empty_string(self):
        """Empty string returns empty."""
        from merge_guard_pre import _strip_non_executable_content

        assert _strip_non_executable_content("") == ""

    def test_no_quotes_unchanged(self):
        """Commands without quotes pass through unchanged."""
        from merge_guard_pre import _strip_non_executable_content

        cmd = "git push --force origin main"
        assert _strip_non_executable_content(cmd) == cmd


class TestFalsePositivePrevention:
    """Verify that dangerous-pattern text in non-executable contexts
    does NOT trigger is_dangerous_command()."""

    def test_echo_double_quoted_not_dangerous(self):
        """echo with quoted dangerous text is not a real command."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('echo "gh pr merge 255"')

    def test_echo_single_quoted_not_dangerous(self):
        """echo with single-quoted dangerous text is not a real command."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("echo 'git push --force origin main'")

    def test_variable_assignment_not_dangerous(self):
        """Variable assignment containing dangerous text is not a command."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('X="gh pr merge"')

    def test_variable_assignment_single_quoted_not_dangerous(self):
        """Single-quoted variable assignment is not a command."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("CMD='git branch -D feat/old'")

    def test_comment_not_dangerous(self):
        """Commented-out dangerous command is not a real command."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("# gh pr merge 42")

    def test_inline_comment_not_dangerous(self):
        """Inline comment after safe command is not dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("git status # gh pr merge 42")

    def test_heredoc_not_dangerous(self):
        """Dangerous text inside heredoc body is not a command."""
        from merge_guard_pre import is_dangerous_command

        cmd = "python3 << 'PYEOF'\nre.compile(r\"\\bgh\\s+pr\\s+merge\\b\")\nPYEOF"
        assert not is_dangerous_command(cmd)

    def test_heredoc_unquoted_not_dangerous(self):
        """Dangerous text inside unquoted heredoc is not a command."""
        from merge_guard_pre import is_dangerous_command

        cmd = "cat << EOF\ngh pr merge 42\nEOF"
        assert not is_dangerous_command(cmd)

    def test_printf_not_dangerous(self):
        """printf with dangerous text is not a command."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('printf "git push --force %s" origin')

    def test_echo_with_flags_not_dangerous(self):
        """echo -e with dangerous text is not a command."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('echo -e "git branch -D feat/old"')

    # --- Real dangerous commands still detected ---

    def test_real_gh_pr_merge_still_detected(self):
        """Bare gh pr merge is still caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr merge 42")

    def test_real_force_push_still_detected(self):
        """Bare git push --force is still caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push --force origin main")

    def test_real_branch_D_still_detected(self):
        """Bare git branch -D is still caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git branch -D feat/old")

    def test_bash_c_still_detected(self):
        """bash -c with dangerous command is still caught (genuinely dangerous)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("bash -c 'gh pr merge 42'")

    def test_chained_after_echo_still_detected(self):
        """Dangerous command chained after echo is caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("echo done && gh pr merge 42")

    def test_semicolon_after_echo_still_detected(self):
        """Dangerous command after semicolon is caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("echo hello; gh pr merge 42")

    def test_env_var_prefix_still_detected(self):
        """Dangerous command with env var prefix is caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("GH_TOKEN=abc gh pr merge 42")

    def test_subshell_still_detected(self):
        """Dangerous command in $() subshell is still caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("$(gh pr merge 42)")

    # --- Edge cases: mixed contexts ---

    def test_echo_then_real_command(self):
        """echo of safe text followed by real dangerous command."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('echo "hello" && gh pr merge 42')

    def test_comment_then_real_command_on_new_line(self):
        """Comment on one line, real command on next."""
        from merge_guard_pre import is_dangerous_command

        cmd = "# just a comment\ngh pr merge 42"
        assert is_dangerous_command(cmd)

    def test_variable_then_real_command(self):
        """Variable assignment followed by real dangerous command."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('X="value" && gh pr merge 42')

    def test_echo_force_push_not_dangerous(self):
        """echo of force push text is not dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('echo "git push --force origin main"')

    def test_echo_branch_delete_not_dangerous(self):
        """echo of branch delete text is not dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('echo "git branch -D feat/old"')

    def test_echo_push_main_not_dangerous(self):
        """echo of push to main text is not dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('echo "git push origin main"')


# =============================================================================
# Bypass vector prevention: execution-via-indirection
# =============================================================================


class TestBypassVectorPrevention:
    """Verify that execution-via-indirection patterns are NOT stripped
    and remain detectable as dangerous commands.

    These are regression tests for bypass vectors introduced by the
    _strip_non_executable_content() stripping — commands that LOOK like
    non-executable contexts but actually execute the dangerous content.
    """

    # --- Pipe to shell interpreter ---

    def test_echo_piped_to_bash_is_dangerous(self):
        """echo piped to bash executes the content."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('echo "gh pr merge 42" | bash')

    def test_echo_piped_to_sh_is_dangerous(self):
        """echo piped to sh executes the content."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('echo "gh pr merge 42" | sh')

    def test_echo_piped_to_zsh_is_dangerous(self):
        """echo piped to zsh executes the content."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('echo "gh pr merge 42" | zsh')

    def test_printf_piped_to_bash_is_dangerous(self):
        """printf piped to bash executes the content."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('printf "gh pr merge 42" | bash')

    def test_printf_piped_to_sh_is_dangerous(self):
        """printf piped to sh executes the content."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('printf "gh pr merge 42" | sh')

    def test_echo_single_quoted_piped_to_bash_is_dangerous(self):
        """echo single-quoted piped to bash executes the content."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("echo 'gh pr merge 42' | bash")

    def test_echo_force_push_piped_to_bash(self):
        """echo of force push piped to bash is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('echo "git push --force origin main" | bash')

    # --- Variable assignment + eval ---

    def test_var_eval_double_ampersand_is_dangerous(self):
        """Variable assignment followed by eval via && is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('CMD="gh pr merge 42" && eval $CMD')

    def test_var_eval_semicolon_is_dangerous(self):
        """Variable assignment followed by eval via ; is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('CMD="gh pr merge 42"; eval $CMD')

    def test_var_source_is_dangerous(self):
        """Variable assignment with source in command is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('CMD="gh pr merge 42"; source /dev/stdin <<< "$CMD"')

    def test_export_eval_is_dangerous(self):
        """export + eval pattern is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('export CMD="gh pr merge 42" && eval $CMD')

    def test_var_single_quoted_eval_is_dangerous(self):
        """Single-quoted variable with eval is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("CMD='gh pr merge 42'; eval $CMD")

    # --- Command substitution in quotes ---

    def test_cmd_sub_in_echo_is_dangerous(self):
        """$() inside echo double quotes executes the command."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('echo "$(gh pr merge 42)"')

    def test_cmd_sub_in_var_assignment_is_dangerous(self):
        """$() inside variable assignment executes the command."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('CMD="$(gh pr merge 42)"')

    def test_backtick_in_echo_is_dangerous(self):
        """Backtick inside echo double quotes executes the command."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('echo "`gh pr merge 42`"')

    def test_backtick_in_var_assignment_is_dangerous(self):
        """Backtick inside variable assignment executes the command."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('CMD="`gh pr merge 42`"')

    # --- Heredoc to shell interpreter ---

    def test_heredoc_to_bash_is_dangerous(self):
        """Heredoc fed to bash executes the body."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("bash << EOF\ngh pr merge 42\nEOF")

    def test_heredoc_to_sh_is_dangerous(self):
        """Heredoc fed to sh executes the body."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("sh << EOF\ngh pr merge 42\nEOF")

    def test_heredoc_to_zsh_is_dangerous(self):
        """Heredoc fed to zsh executes the body."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("zsh << EOF\ngh pr merge 42\nEOF")

    # --- Ensure non-shell heredocs are still stripped (false positive prevention) ---

    def test_heredoc_to_cat_not_dangerous(self):
        """Heredoc to cat is not executable — still stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("cat << EOF\ngh pr merge 42\nEOF")

    def test_heredoc_to_python_not_dangerous(self):
        """Heredoc to python is not executable by shell — still stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "python3 << EOF\nre.compile(r'gh pr merge')\nEOF"
        )

    # --- Ensure normal echo/var false-positive prevention still works ---

    def test_echo_not_piped_still_stripped(self):
        """echo NOT piped to shell is still stripped (false positive fix)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('echo "gh pr merge 42"')

    def test_var_no_eval_still_stripped(self):
        """Variable without eval is still stripped (false positive fix)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('CMD="gh pr merge 42"')


class TestStripHelpers:
    """Tests for the execution-via-indirection detection helpers."""

    def test_has_pipe_to_bash(self):
        from merge_guard_pre import _has_pipe_to_shell

        assert _has_pipe_to_shell("echo x | bash")
        assert _has_pipe_to_shell("echo x | sh")
        assert _has_pipe_to_shell("echo x | zsh")
        assert _has_pipe_to_shell("echo x |bash")
        assert not _has_pipe_to_shell("echo x | grep bash")
        assert not _has_pipe_to_shell("echo x")

    def test_has_pipe_to_xargs_shell(self):
        from merge_guard_pre import _has_pipe_to_shell

        assert _has_pipe_to_shell("echo x | xargs bash")
        assert _has_pipe_to_shell("echo x | xargs sh")
        assert _has_pipe_to_shell("echo x | xargs zsh")
        assert _has_pipe_to_shell("echo x | xargs -I {} bash -c {}")
        assert not _has_pipe_to_shell("echo x | xargs grep")
        assert not _has_pipe_to_shell("echo x | xargs echo")

    def test_has_eval_or_source(self):
        from merge_guard_pre import _has_eval_or_source

        assert _has_eval_or_source("eval $CMD")
        assert _has_eval_or_source('CMD="x" && eval $CMD')
        assert _has_eval_or_source("source script.sh")
        assert not _has_eval_or_source('CMD="x"')
        assert not _has_eval_or_source("echo evaluation")

    def test_has_command_substitution(self):
        from merge_guard_pre import _has_command_substitution

        assert _has_command_substitution("$(gh pr merge 42)")
        assert _has_command_substitution("`gh pr merge 42`")
        assert not _has_command_substitution("gh pr merge 42")
        assert not _has_command_substitution("safe string")

    def test_has_process_substitution_to_shell(self):
        from merge_guard_pre import _has_process_substitution_to_shell

        assert _has_process_substitution_to_shell("bash <(echo x)")
        assert _has_process_substitution_to_shell("sh <(echo x)")
        assert _has_process_substitution_to_shell("zsh <(echo x)")
        assert not _has_process_substitution_to_shell("cat <(echo x)")
        assert not _has_process_substitution_to_shell("grep <(echo x)")
        assert not _has_process_substitution_to_shell("echo x")


class TestAdditionalDangerousPatterns:
    """Additional dangerous command detection tests (minor gaps from review)."""

    def test_bash_c_double_quoted_is_dangerous(self):
        """bash -c with double-quoted dangerous command is caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('bash -c "gh pr merge 42"')

    def test_sh_c_single_quoted_is_dangerous(self):
        """sh -c with single-quoted dangerous command is caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("sh -c 'gh pr merge 42'")

    def test_eval_double_quoted_is_dangerous(self):
        """eval with double-quoted dangerous command is caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('eval "gh pr merge 42"')

    def test_echo_backtick_substitution_is_dangerous(self):
        """echo with backtick command substitution is caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('echo "`gh pr merge 42`"')


# =============================================================================
# Bare variable expansion bypass prevention
# =============================================================================


class TestBareVariableExpansion:
    """Verify that bare $VAR / ${VAR} expansion of a variable containing
    dangerous text is detected, even without eval/source."""

    def test_bare_dollar_var_after_ampersand(self):
        """CMD="gh pr merge 42" && $CMD — bare expansion executes."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('CMD="gh pr merge 42" && $CMD')

    def test_bare_dollar_var_after_semicolon(self):
        """CMD="gh pr merge 42"; $CMD — bare expansion executes."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('CMD="gh pr merge 42"; $CMD')

    def test_bare_dollar_brace_var(self):
        """CMD="gh pr merge 42" && ${CMD} — braced expansion executes."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('CMD="gh pr merge 42" && ${CMD}')

    def test_bare_var_single_quoted(self):
        """CMD='gh pr merge 42'; $CMD — single-quoted + bare expansion."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("CMD='gh pr merge 42'; $CMD")

    def test_var_without_expansion_still_stripped(self):
        """CMD="gh pr merge 42" alone (no $CMD) is still a false positive fix."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('CMD="gh pr merge 42"')

    def test_different_var_expanded_not_affected(self):
        """CMD="gh pr merge 42" with $OTHER expanded — CMD still stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('CMD="gh pr merge 42" && $OTHER')

    def test_var_is_expanded_helper(self):
        """_var_is_expanded detects $VAR and ${VAR} patterns."""
        from merge_guard_pre import _var_is_expanded

        assert _var_is_expanded("CMD", 'X && $CMD')
        assert _var_is_expanded("CMD", 'X && ${CMD}')
        assert not _var_is_expanded("CMD", 'X && $OTHER')
        assert not _var_is_expanded("CMD", 'CMD="value"')


# =============================================================================
# Heredoc <<- with tab-indented closing marker
# =============================================================================


class TestHeredocIndentedMarker:
    """Verify that <<- heredocs with tab-indented closing markers are
    properly stripped (non-shell targets) or preserved (shell targets)."""

    def test_heredoc_dash_indented_to_cat(self):
        """<<- heredoc with tab-indented EOF to cat is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("cat <<- EOF\n\tgh pr merge 42\n\tEOF")

    def test_heredoc_dash_non_indented_to_cat(self):
        """<<- heredoc with non-indented EOF to cat is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("cat <<- EOF\n\tgh pr merge 42\nEOF")

    def test_heredoc_dash_indented_to_bash(self):
        """<<- heredoc with tab-indented EOF to bash is preserved (dangerous)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("bash <<- EOF\n\tgh pr merge 42\n\tEOF")

    def test_heredoc_dash_non_indented_to_bash(self):
        """<<- heredoc with non-indented EOF to bash is preserved (dangerous)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("bash <<- EOF\n\tgh pr merge 42\nEOF")

    def test_strip_helper_indented_marker(self):
        """_strip_non_executable_content handles tab-indented markers."""
        from merge_guard_pre import _strip_non_executable_content

        cmd = "cat <<- EOF\n\tgh pr merge 42\n\tEOF"
        result = _strip_non_executable_content(cmd)
        assert "gh pr merge" not in result


# =============================================================================
# git commit -m false positive prevention
# =============================================================================


class TestGitCommitMessageStripping:
    """Verify that git commit -m messages containing dangerous text
    are stripped (false positive prevention) while real commands are detected."""

    def test_commit_msg_merge_text(self):
        """git commit -m with merge text is not dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('git commit -m "gh pr merge 42"')

    def test_commit_msg_force_push_text(self):
        """git commit -m with force push text is not dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('git commit -m "git push --force origin main"')

    def test_commit_msg_branch_delete_text(self):
        """git commit -m with branch delete text is not dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('git commit -m "git branch -D feat/old"')

    def test_commit_msg_single_quoted(self):
        """git commit -m with single-quoted message is not dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("git commit -m 'gh pr merge 42'")

    def test_commit_msg_cmd_substitution_is_dangerous(self):
        """git commit -m with command substitution IS dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('git commit -m "$(gh pr merge 42)"')

    def test_commit_msg_push_main_text(self):
        """git commit -m with push-to-main text is not dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('git commit -m "git push origin main"')

    def test_real_merge_still_detected(self):
        """Real gh pr merge is still detected alongside commit."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('git commit -m "done" && gh pr merge 42')

    def test_strip_helper_commit_msg(self):
        """_strip_non_executable_content strips commit message."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content('git commit -m "gh pr merge 42"')
        assert "gh pr merge" not in result


# =============================================================================
# Edge cases: merge_guard_post
# =============================================================================


class TestMergeQuestionEdgeCases:
    """Edge cases for merge question detection."""

    def test_merge_substring_no_longer_false_fires(self):
        """KD-9: is_merge_question is command-driven, so the old 'merge'-as-
        substring false-positive on 'emerged' is ELIMINATED — prose with no
        embedded command does not fire."""
        from merge_guard_post import is_merge_question

        assert is_merge_question("The data emerged from the pipeline") is False

    # (removed the is_affirmative test methods — merge_guard_post.is_affirmative
    #  and its AFFIRMATIVE_PATTERNS regex were removed as production-orphaned dead
    #  code; OPTION-mode approval is an exact non-decline label match, not a
    #  free-text word allowlist. is_merge_question coverage stays above.)

    # (removed test_extract_pull_request_text / test_extract_branch_with_dots /
    #  test_extract_branch_with_underscores / test_extract_quoted_branch — they
    #  exercised the dropped prose extractor merge_guard_post.extract_context.
    #  Command-side target extraction is covered by extract_command_context in
    #  the bidirectional suite test_merge_guard_auth_symmetry.py.)


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
            # Use a valid context (operation_type satisfies sparse-context guard);
            # the test exercises the readonly-dir failure path, not the guard.
            result = write_token({"operation_type": "merge"}, token_dir=readonly_dir)
            assert result is None
        finally:
            os.chmod(str(readonly_dir), 0o755)

    def test_write_token_collision_uses_fallback(self, tmp_path):
        """O_EXCL fallback path remains reachable for true same-microsecond races.

        Layer 5 (invariant I-1) retires unused tokens before O_EXCL, so an
        unused pre-existing token cannot cause O_EXCL to fail under normal
        use. The microsecond-suffix fallback path is still needed for the
        true race between cleanup completion and O_EXCL, or for any
        scenario where cleanup is bypassed (e.g., concurrent writer in a
        different process landed a file in the microsecond window).

        Simulate the latter by stubbing cleanup_unused_tokens to no-op
        for the duration of one write_token call — this models a
        cleanup-failure or concurrent-race scenario and pins the O_EXCL
        fallback behavior independently of Layer 5.
        """
        import merge_guard_post
        from merge_guard_post import write_token

        # Pre-create the file that write_token will try to create
        now = time.time()
        timestamp = int(now)
        preexisting = tmp_path / f"merge-authorized-{timestamp}"
        preexisting.write_text("taken")

        # Stub Layer 5 cleanup so the collision survives to O_EXCL. This
        # mirrors a transient cleanup failure or a true concurrent-writer
        # race on the microsecond window.
        with patch.object(merge_guard_post, "_cleanup_unused_tokens", lambda _td: None), \
             patch("merge_guard_post.time") as mock_time:
            mock_time.time.return_value = now
            result = write_token({"test": True, "operation_type": "merge"}, token_dir=tmp_path)

        # Must succeed with fallback name (not None)
        assert result is not None
        assert Path(result).exists()
        assert Path(result) != preexisting


# =============================================================================
# Edge cases: main() entry points
# =============================================================================


class TestPostMainEdgeCases:
    """Edge cases for merge_guard_post.main()."""

    def test_tool_response_empty_dict(self, tmp_path):
        """tool_response as empty dict — no token created."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_response": {},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("merge_guard_post.write_token") as mock_write_token:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0
        # Cause-of-no-token: empty answers dict produced empty answer string,
        # which is falsy, so the merge-question branch did not reach write_token.
        mock_write_token.assert_not_called()

    def test_tool_response_with_answers_key(self, tmp_path):
        """tool_response dict with 'answers' key (actual AskUserQuestion format)."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{
                "question": "Merge the PR?",
                "options": [
                    {"label": "Yes, merge", "description": "Run `gh pr merge 10`"},
                    {"label": "Cancel", "description": "Abort"},
                ],
            }]},
            "tool_response": {"answers": {"Merge the PR?": "Yes, merge"}},
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
            "tool_response": {"answers": {"anything": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_tool_response_none(self, tmp_path):
        """tool_response as None — non-dict, exits early, no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Merge?"}]},
            "tool_response": None,
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("merge_guard_post.write_token") as mock_write_token:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0
        # Cause-of-no-token: non-dict tool_response triggered isinstance early-exit
        # before the merge-question branch could reach write_token.
        mock_write_token.assert_not_called()

    def test_tool_response_integer(self, tmp_path):
        """tool_response as integer — non-dict, exits early, no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Merge?"}]},
            "tool_response": 42,
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("merge_guard_post.write_token") as mock_write_token:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0
        # Cause-of-no-token: non-dict tool_response triggered isinstance early-exit
        # before the merge-question branch could reach write_token.
        mock_write_token.assert_not_called()

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
        # Issue #658: hookEventName is the load-bearing schema field; without
        # it the harness silently rejects the deny block and merges proceed.
        assert hook_output["hookEventName"] == "PreToolUse"


# =============================================================================
# Security tests
# =============================================================================


class TestTokenSecurity:
    """Security-focused tests for the merge guard token mechanism."""

    def test_token_file_not_world_readable(self, tmp_path):
        """Token file must be 0o600 — not readable by others."""
        from merge_guard_post import write_token

        result = write_token({"sensitive": True, "operation_type": "merge"}, token_dir=tmp_path)
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

        result = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)
        with open(result) as f:
            data = json.load(f)

        assert isinstance(data["created_at"], (int, float))
        assert isinstance(data["expires_at"], (int, float))
        assert isinstance(data["context"], dict)
        assert data["expires_at"] > data["created_at"]

    def test_token_ttl_is_15_minutes(self):
        """TOKEN_TTL constant must be 900 seconds (15 minutes)."""
        from merge_guard_post import TOKEN_TTL as post_ttl
        from merge_guard_pre import TOKEN_TTL as pre_ttl

        assert post_ttl == 900
        assert pre_ttl == 900

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

        large_context = {"data": "x" * 10000, "operation_type": "merge"}
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
            "tool_response": {"answers": {"Merge PR #1?": "yes"}},
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
        # Issue #658: fail-closed path also requires hookEventName, otherwise
        # the harness silently rejects the deny and the merge proceeds.
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


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
        """merge_guard_post.py must be registered under PostToolUse for
        both AskUserQuestion AND Bash (matcher widened per #797 Layer 1).

        The matcher is a regex-style alternation `AskUserQuestion|Bash`
        so the same hook handles BOTH tool events: token write on
        AskUserQuestion approval, token retirement on successful Bash
        `gh pr merge`.
        """
        entries = hooks_config["hooks"].get("PostToolUse", [])
        matched_hooks = []
        for entry in entries:
            matcher = entry.get("matcher", "")
            # Accept any matcher pattern that includes AskUserQuestion as
            # an alternation alternative or as the literal value.
            if "AskUserQuestion" in matcher.split("|"):
                for hook in entry.get("hooks", []):
                    matched_hooks.append(hook.get("command", ""))

        assert any("merge_guard_post.py" in cmd for cmd in matched_hooks), (
            "merge_guard_post.py not found in PostToolUse AskUserQuestion hooks"
        )

    def test_merge_guard_post_matcher_includes_bash(self, hooks_config):
        """merge_guard_post.py matcher must also include Bash (Layer 1 #797).

        Layer 1 invariant I-2 requires PostToolUse(Bash) to retire the
        consuming token on successful gh pr merge. The matcher widening
        from `AskUserQuestion` to `AskUserQuestion|Bash` is the
        registration half of the atomic registration+handler pair.

        # counter-test: revert hooks.json matcher to just `AskUserQuestion`
        #               → this test goes RED; Layer 1 handler never fires.
        # expected RED cardinality: {1}
        """
        entries = hooks_config["hooks"].get("PostToolUse", [])
        bash_alternation_found = False
        for entry in entries:
            matcher = entry.get("matcher", "")
            if "Bash" in matcher.split("|"):
                for hook in entry.get("hooks", []):
                    if "merge_guard_post.py" in hook.get("command", ""):
                        bash_alternation_found = True
                        break
        assert bash_alternation_found, (
            "merge_guard_post.py matcher must include Bash alternation "
            "for Layer 1 I-2 enforcement"
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
        """merge_guard_post.py must be synchronous — token must be written
        before next tool call AND retirement must complete before next
        Bash precheck reads the token store (Layer 1 #797).
        """
        entries = hooks_config["hooks"].get("PostToolUse", [])
        for entry in entries:
            matcher = entry.get("matcher", "")
            # Match the widened `AskUserQuestion|Bash` alternation as well
            # as legacy literal `AskUserQuestion`.
            if "AskUserQuestion" in matcher.split("|"):
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
# API-based branch deletion detection (gh api + curl DELETE git/refs)
# =============================================================================


class TestAPIBranchDeletion:
    """Tests for API-based branch deletion via DELETE to git/refs endpoint."""

    # --- gh api: dangerous DELETE commands ---

    def test_gh_api_delete_branch_ref(self):
        """gh api -X DELETE to git/refs/heads is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X DELETE repos/owner/repo/git/refs/heads/feature-branch"
        )

    def test_gh_api_delete_branch_ref_method_flag(self):
        """gh api --method DELETE to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api --method DELETE repos/owner/repo/git/refs/heads/feature"
        )

    def test_gh_api_delete_tag_ref(self):
        """gh api -X DELETE to git/refs/tags is dangerous (defense-in-depth)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X DELETE repos/owner/repo/git/refs/tags/v1.0"
        )

    def test_gh_api_delete_ref_case_insensitive(self):
        """gh api DELETE detection is case-insensitive for HTTP method."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X delete repos/owner/repo/git/refs/heads/feature"
        )

    def test_gh_api_delete_ref_mixed_case(self):
        """gh api Delete (mixed case) is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X Delete repos/owner/repo/git/refs/heads/feature"
        )

    def test_gh_api_delete_ref_method_after_url(self):
        """gh api with -X DELETE after the URL is still detected (lookahead)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/feature -X DELETE"
        )

    def test_gh_api_delete_ref_method_flag_after_url(self):
        """gh api with --method DELETE after the URL is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/feature --method DELETE"
        )

    def test_gh_api_delete_ref_with_repo_flag(self):
        """gh --repo owner/repo api -X DELETE git/refs is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo api -X DELETE repos/owner/repo/git/refs/heads/feature"
        )

    def test_gh_api_delete_ref_with_R_flag(self):
        """gh -R owner/repo api -X DELETE git/refs is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh -R owner/repo api -X DELETE repos/owner/repo/git/refs/heads/feature"
        )

    def test_gh_api_delete_ref_with_hostname_flag(self):
        """gh --hostname host api -X DELETE git/refs is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --hostname github.example.com api -X DELETE repos/owner/repo/git/refs/heads/feature"
        )

    def test_gh_api_delete_ref_with_multiple_global_flags(self):
        """gh --repo X --hostname Y api -X DELETE git/refs is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo --hostname github.example.com api -X DELETE repos/owner/repo/git/refs/heads/feature"
        )

    # --- curl: dangerous DELETE commands ---

    def test_curl_delete_branch_ref(self):
        """curl -X DELETE to git/refs API endpoint is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl -X DELETE https://api.github.com/repos/owner/repo/git/refs/heads/feature"
        )

    def test_curl_delete_ref_request_flag(self):
        """curl --request DELETE to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl --request DELETE https://api.github.com/repos/owner/repo/git/refs/heads/feature"
        )

    def test_curl_delete_ref_case_insensitive(self):
        """curl DELETE detection is case-insensitive."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl -X delete https://api.github.com/repos/owner/repo/git/refs/heads/feature"
        )

    def test_curl_delete_ref_method_after_url(self):
        """curl with -X DELETE after the URL is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl https://api.github.com/repos/owner/repo/git/refs/heads/feature -X DELETE"
        )

    def test_curl_delete_ref_request_after_url(self):
        """curl with --request DELETE after the URL is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl https://api.github.com/repos/owner/repo/git/refs/heads/feature --request DELETE"
        )

    def test_curl_delete_tag_ref(self):
        """curl -X DELETE to git/refs/tags is dangerous (defense-in-depth)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl -X DELETE https://api.github.com/repos/owner/repo/git/refs/tags/v1.0"
        )

    def test_curl_delete_ref_with_auth_header(self):
        """curl with auth header and DELETE to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl -H "Authorization: token ghp_xxx" -X DELETE https://api.github.com/repos/owner/repo/git/refs/heads/feature'
        )

    # --- Safe GET operations (must NOT be detected) ---

    def test_gh_api_get_ref_is_safe(self):
        """gh api to git/refs without mutating method is safe (default GET)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/main"
        )

    def test_gh_api_explicit_get_ref_is_safe(self):
        """gh api -X GET to git/refs is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api -X GET repos/owner/repo/git/refs/heads/main"
        )

    def test_gh_api_list_refs_is_safe(self):
        """gh api to git/refs (list all refs) is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/git/refs"
        )

    def test_curl_get_ref_is_safe(self):
        """curl to git/refs without -X flag is safe (default GET)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl https://api.github.com/repos/owner/repo/git/refs/heads/main"
        )

    def test_curl_explicit_get_ref_is_safe(self):
        """curl -X GET to git/refs is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl -X GET https://api.github.com/repos/owner/repo/git/refs/heads/main"
        )


# =============================================================================
# API-based ref mutation / force push detection (PATCH/POST/PUT to git/refs)
# =============================================================================


class TestAPIRefMutation:
    """Tests for API-based ref mutation via PATCH/POST/PUT to git/refs endpoint."""

    # --- gh api: dangerous mutating commands ---

    def test_gh_api_patch_ref(self):
        """gh api -X PATCH to git/refs is dangerous (ref update)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X PATCH repos/owner/repo/git/refs/heads/feature -f sha=abc123 -f force=true"
        )

    def test_gh_api_post_ref(self):
        """gh api -X POST to git/refs is dangerous (ref creation)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X POST repos/owner/repo/git/refs -f ref=refs/heads/new-branch -f sha=abc123"
        )

    def test_gh_api_put_ref(self):
        """gh api -X PUT to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X PUT repos/owner/repo/git/refs/heads/feature -f sha=abc123"
        )

    def test_gh_api_method_patch_ref(self):
        """gh api --method PATCH to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api --method PATCH repos/owner/repo/git/refs/heads/feature -f sha=abc123"
        )

    def test_gh_api_method_post_ref(self):
        """gh api --method POST to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api --method POST repos/owner/repo/git/refs -f ref=refs/heads/new -f sha=abc"
        )

    def test_gh_api_method_put_ref(self):
        """gh api --method PUT to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api --method PUT repos/owner/repo/git/refs/heads/feature -f sha=abc123"
        )

    def test_gh_api_patch_ref_case_insensitive(self):
        """gh api PATCH detection is case-insensitive."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X patch repos/owner/repo/git/refs/heads/feature -f sha=abc"
        )

    def test_gh_api_post_ref_case_insensitive(self):
        """gh api POST detection is case-insensitive."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X Post repos/owner/repo/git/refs/heads/feature -f sha=abc"
        )

    def test_gh_api_put_ref_case_insensitive(self):
        """gh api PUT detection is case-insensitive."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X put repos/owner/repo/git/refs/heads/feature -f sha=abc"
        )

    def test_gh_api_patch_ref_method_after_url(self):
        """gh api with -X PATCH after URL is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/feature -X PATCH -f sha=abc123"
        )

    def test_gh_api_ref_mutation_with_repo_flag(self):
        """gh --repo flag with api ref mutation is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo api -X PATCH repos/owner/repo/git/refs/heads/feature -f sha=abc"
        )

    def test_gh_api_ref_mutation_with_R_flag(self):
        """gh -R flag with api ref mutation is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh -R owner/repo api -X POST repos/owner/repo/git/refs -f ref=refs/heads/new -f sha=abc"
        )

    def test_gh_api_ref_mutation_with_hostname(self):
        """gh --hostname flag with api ref mutation is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --hostname github.example.com api -X PATCH repos/owner/repo/git/refs/heads/feature -f sha=abc"
        )

    # --- curl: dangerous mutating commands ---

    def test_curl_patch_ref(self):
        """curl -X PATCH to git/refs API is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl -X PATCH https://api.github.com/repos/owner/repo/git/refs/heads/feature -d \'{"sha":"abc","force":true}\''
        )

    def test_curl_post_ref(self):
        """curl -X POST to git/refs API is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl -X POST https://api.github.com/repos/owner/repo/git/refs -d \'{"ref":"refs/heads/new","sha":"abc"}\''
        )

    def test_curl_put_ref(self):
        """curl -X PUT to git/refs API is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl -X PUT https://api.github.com/repos/owner/repo/git/refs/heads/feature"
        )

    def test_curl_request_patch_ref(self):
        """curl --request PATCH to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl --request PATCH https://api.github.com/repos/owner/repo/git/refs/heads/feature"
        )

    def test_curl_request_post_ref(self):
        """curl --request POST to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl --request POST https://api.github.com/repos/owner/repo/git/refs"
        )

    def test_curl_request_put_ref(self):
        """curl --request PUT to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl --request PUT https://api.github.com/repos/owner/repo/git/refs/heads/feature"
        )

    def test_curl_patch_ref_case_insensitive(self):
        """curl PATCH detection is case-insensitive."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl -X patch https://api.github.com/repos/owner/repo/git/refs/heads/feature"
        )

    def test_curl_ref_mutation_method_after_url(self):
        """curl with -X PATCH after URL is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl https://api.github.com/repos/owner/repo/git/refs/heads/feature -X PATCH"
        )

    def test_curl_ref_mutation_request_after_url(self):
        """curl with --request POST after URL is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl https://api.github.com/repos/owner/repo/git/refs/heads/feature --request POST"
        )

    # --- Safe operations (must NOT be detected) ---

    def test_gh_api_get_ref_info_is_safe(self):
        """gh api to git/refs without mutating method is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/feature"
        )

    def test_gh_api_explicit_get_ref_info_is_safe(self):
        """gh api -X GET to git/refs is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api -X GET repos/owner/repo/git/refs/heads/feature"
        )

    def test_curl_get_ref_info_is_safe(self):
        """curl to git/refs without -X flag is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl https://api.github.com/repos/owner/repo/git/refs/heads/feature"
        )

    def test_curl_explicit_get_ref_is_safe(self):
        """curl -X GET to git/refs is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl -X GET https://api.github.com/repos/owner/repo/git/refs/heads/feature"
        )


# =============================================================================
# API-based push to main/master detection (PATCH/POST/PUT to git/refs/heads/main|master)
# =============================================================================


class TestAPIPushToMain:
    """Tests for API-based push to main/master via mutating method to git/refs/heads/main|master."""

    # --- gh api: dangerous mutating commands to main/master ---

    def test_gh_api_patch_main_ref(self):
        """gh api -X PATCH to git/refs/heads/main is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X PATCH repos/owner/repo/git/refs/heads/main -f sha=abc123"
        )

    def test_gh_api_patch_master_ref(self):
        """gh api -X PATCH to git/refs/heads/master is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X PATCH repos/owner/repo/git/refs/heads/master -f sha=abc123"
        )

    def test_gh_api_post_main_ref(self):
        """gh api -X POST to git/refs/heads/main is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X POST repos/owner/repo/git/refs/heads/main -f sha=abc123"
        )

    def test_gh_api_put_main_ref(self):
        """gh api -X PUT to git/refs/heads/main is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X PUT repos/owner/repo/git/refs/heads/main -f sha=abc123"
        )

    def test_gh_api_method_patch_main_ref(self):
        """gh api --method PATCH to git/refs/heads/main is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api --method PATCH repos/owner/repo/git/refs/heads/main -f sha=abc123"
        )

    def test_gh_api_method_post_master_ref(self):
        """gh api --method POST to git/refs/heads/master is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api --method POST repos/owner/repo/git/refs/heads/master -f sha=abc123"
        )

    def test_gh_api_patch_main_case_insensitive(self):
        """gh api PATCH detection for main is case-insensitive."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X patch repos/owner/repo/git/refs/heads/main -f sha=abc"
        )

    def test_gh_api_patch_main_method_after_url(self):
        """gh api with -X PATCH after main URL is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/main -X PATCH -f sha=abc123"
        )

    def test_gh_api_patch_main_with_repo_flag(self):
        """gh --repo flag with api PATCH to main is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo api -X PATCH repos/owner/repo/git/refs/heads/main -f sha=abc"
        )

    def test_gh_api_patch_main_with_R_flag(self):
        """gh -R flag with api PATCH to main is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh -R owner/repo api -X PATCH repos/owner/repo/git/refs/heads/main -f sha=abc"
        )

    def test_gh_api_patch_master_with_hostname(self):
        """gh --hostname flag with api PATCH to master is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --hostname github.example.com api -X PATCH repos/owner/repo/git/refs/heads/master -f sha=abc"
        )

    def test_gh_api_patch_main_with_multiple_global_flags(self):
        """gh --repo X --hostname Y api PATCH to main is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo --hostname github.example.com api -X PATCH repos/owner/repo/git/refs/heads/main -f sha=abc"
        )

    # --- curl: dangerous mutating commands to main/master ---

    def test_curl_patch_main_ref(self):
        """curl -X PATCH to git/refs/heads/main is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl -X PATCH https://api.github.com/repos/owner/repo/git/refs/heads/main"
        )

    def test_curl_patch_master_ref(self):
        """curl -X PATCH to git/refs/heads/master is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl -X PATCH https://api.github.com/repos/owner/repo/git/refs/heads/master"
        )

    def test_curl_post_main_ref(self):
        """curl -X POST to git/refs/heads/main is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl -X POST https://api.github.com/repos/owner/repo/git/refs/heads/main"
        )

    def test_curl_put_main_ref(self):
        """curl -X PUT to git/refs/heads/main is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl -X PUT https://api.github.com/repos/owner/repo/git/refs/heads/main"
        )

    def test_curl_request_patch_main(self):
        """curl --request PATCH to git/refs/heads/main is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl --request PATCH https://api.github.com/repos/owner/repo/git/refs/heads/main"
        )

    def test_curl_request_patch_master(self):
        """curl --request PATCH to git/refs/heads/master is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl --request PATCH https://api.github.com/repos/owner/repo/git/refs/heads/master"
        )

    def test_curl_patch_main_case_insensitive(self):
        """curl PATCH to main detection is case-insensitive."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl -X patch https://api.github.com/repos/owner/repo/git/refs/heads/main"
        )

    def test_curl_patch_main_method_after_url(self):
        """curl with -X PATCH after main URL is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl https://api.github.com/repos/owner/repo/git/refs/heads/main -X PATCH"
        )

    def test_curl_request_post_master_after_url(self):
        """curl with --request POST after master URL is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl https://api.github.com/repos/owner/repo/git/refs/heads/master --request POST"
        )

    # --- Safe operations (must NOT be detected) ---

    def test_gh_api_get_main_ref_is_safe(self):
        """gh api to git/refs/heads/main without mutating method is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/main"
        )

    def test_gh_api_explicit_get_main_ref_is_safe(self):
        """gh api -X GET to git/refs/heads/main is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api -X GET repos/owner/repo/git/refs/heads/main"
        )

    def test_gh_api_get_master_ref_is_safe(self):
        """gh api to git/refs/heads/master without mutating method is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/master"
        )

    def test_curl_get_main_ref_is_safe(self):
        """curl to git/refs/heads/main without -X flag is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl https://api.github.com/repos/owner/repo/git/refs/heads/main"
        )

    def test_curl_explicit_get_main_is_safe(self):
        """curl -X GET to git/refs/heads/main is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl -X GET https://api.github.com/repos/owner/repo/git/refs/heads/main"
        )

    def test_gh_api_patch_feature_branch_is_dangerous(self):
        """gh api PATCH to feature branch ref is caught by generic ref mutation pattern."""
        from merge_guard_pre import is_dangerous_command

        # Feature branch ref mutation is dangerous — caught by the generic git/refs pattern.
        assert is_dangerous_command(
            "gh api -X PATCH repos/owner/repo/git/refs/heads/feature -f sha=abc"
        )


# =============================================================================
# API bypass — false positive prevention via stripping pipeline
# =============================================================================


class TestAPIBypassFalsePositivePrevention:
    """Tests that the stripping pipeline correctly handles API bypass patterns
    in non-executable contexts (echo, variable assignments, etc.)."""

    def test_echo_gh_api_delete_ref_stripped(self):
        """echo 'gh api -X DELETE ...' is stripped and NOT flagged."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'echo "gh api -X DELETE repos/owner/repo/git/refs/heads/main"'
        )

    def test_echo_single_quote_gh_api_delete_stripped(self):
        """echo 'gh api -X DELETE ...' (single quotes) is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "echo 'gh api -X DELETE repos/owner/repo/git/refs/heads/main'"
        )

    def test_var_assignment_gh_api_delete_stripped(self):
        """VAR='gh api -X DELETE ...' is stripped and NOT flagged."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'CMD="gh api -X DELETE repos/owner/repo/git/refs/heads/main"'
        )

    def test_var_assignment_single_quote_stripped(self):
        """VAR='gh api -X DELETE ...' (single quotes) is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "CMD='gh api -X DELETE repos/owner/repo/git/refs/heads/main'"
        )

    def test_echo_curl_delete_ref_stripped(self):
        """echo 'curl -X DELETE ...' is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'echo "curl -X DELETE https://api.github.com/repos/o/r/git/refs/heads/main"'
        )

    def test_echo_gh_api_patch_ref_stripped(self):
        """echo 'gh api -X PATCH ... git/refs ...' is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'echo "gh api -X PATCH repos/owner/repo/git/refs/heads/main -f sha=abc"'
        )

    def test_var_assignment_curl_patch_main_stripped(self):
        """VAR='curl -X PATCH ... git/refs/heads/main' is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'CMD="curl -X PATCH https://api.github.com/repos/o/r/git/refs/heads/main"'
        )

    def test_comment_gh_api_delete_stripped(self):
        """# gh api -X DELETE ... is stripped as a comment."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "# gh api -X DELETE repos/owner/repo/git/refs/heads/main"
        )

    def test_git_commit_msg_gh_api_delete_stripped(self):
        """git commit -m 'gh api -X DELETE ...' is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'git commit -m "Blocked: gh api -X DELETE repos/o/r/git/refs/heads/main"'
        )

    def test_printf_gh_api_patch_ref_stripped(self):
        """printf 'gh api -X PATCH ... git/refs ...' is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'printf "gh api -X PATCH repos/owner/repo/git/refs/heads/main"'
        )

    # --- Guard: echo piped to shell preserves content ---

    def test_echo_gh_api_delete_piped_to_bash_preserved(self):
        """echo 'gh api -X DELETE ...' | bash is NOT stripped (executes)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'echo "gh api -X DELETE repos/owner/repo/git/refs/heads/main" | bash'
        )

    def test_echo_curl_patch_ref_piped_to_sh_preserved(self):
        """echo 'curl ...' | sh is NOT stripped."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'echo "curl -X PATCH https://api.github.com/repos/o/r/git/refs/heads/main" | sh'
        )

    # --- Guard: eval preserves variable content ---

    def test_var_with_eval_preserved(self):
        """CMD='gh api -X DELETE ...' && eval $CMD is NOT stripped."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'CMD="gh api -X DELETE repos/owner/repo/git/refs/heads/main" && eval $CMD'
        )

    # --- Guard: command substitution inside echo preserves ---

    def test_echo_with_command_substitution_preserved(self):
        """echo \"$(gh api -X DELETE ...)\" is NOT stripped."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'echo "$(gh api -X DELETE repos/owner/repo/git/refs/heads/main)"'
        )

    # --- Non-ref API operations are safe ---

    def test_gh_api_issues_close_is_safe(self):
        """gh api to close an issue (not PR) is safe — no git/refs involvement."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api -X PATCH repos/owner/repo/issues/42 -f state=closed"
        )

    def test_gh_api_read_pr_info_is_safe(self):
        """gh api to read PR info is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/pulls/42"
        )

    def test_curl_read_refs_with_jq_is_safe(self):
        """curl to read refs piped to jq is safe (no mutating method)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl https://api.github.com/repos/owner/repo/git/refs | jq '.'"
        )


# =============================================================================
# API bypass — line continuation normalization
# =============================================================================


class TestAPIBypassLineContinuation:
    """Tests that line continuations in API bypass commands are normalized."""

    def test_gh_api_delete_ref_line_continuation(self):
        """gh api -X DELETE \\ repos/.../git/refs is detected after normalization."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X DELETE \\\nrepos/owner/repo/git/refs/heads/feature"
        )

    def test_gh_api_patch_main_line_continuation(self):
        """gh api -X PATCH \\ to main is detected after normalization."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X PATCH \\\nrepos/owner/repo/git/refs/heads/main \\\n-f sha=abc123"
        )

    def test_curl_delete_ref_line_continuation(self):
        """curl -X DELETE \\ to git/refs is detected after normalization."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl -X DELETE \\\nhttps://api.github.com/repos/owner/repo/git/refs/heads/feature"
        )

    def test_gh_api_with_repo_flag_line_continuation(self):
        """gh --repo \\ api -X PATCH git/refs is detected after normalization."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo \\\napi -X PATCH repos/owner/repo/git/refs/heads/main -f sha=abc"
        )


# =============================================================================
# API bypass — authorization flow integration
# =============================================================================


class TestAPIBypassAuthorizationFlow:
    """Integration tests for API bypass commands with the token authorization system."""

    def test_gh_api_delete_ref_blocked_without_token(self, tmp_path):
        """gh api DELETE to git/refs is blocked without authorization token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization(
            "gh api -X DELETE repos/owner/repo/git/refs/heads/feature",
            token_dir=tmp_path,
        )
        assert result is not None
        assert "approval" in result.lower() or "AskUserQuestion" in result

    def test_gh_api_patch_ref_blocked_without_token(self, tmp_path):
        """gh api PATCH to git/refs is blocked without token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization(
            "gh api -X PATCH repos/owner/repo/git/refs/heads/feature -f sha=abc",
            token_dir=tmp_path,
        )
        assert result is not None
        assert "approval" in result.lower() or "AskUserQuestion" in result

    def test_gh_api_patch_main_blocked_without_token(self, tmp_path):
        """gh api PATCH to main is blocked without token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization(
            "gh api -X PATCH repos/owner/repo/git/refs/heads/main -f sha=abc",
            token_dir=tmp_path,
        )
        assert result is not None
        assert "approval" in result.lower() or "AskUserQuestion" in result

    def test_curl_delete_ref_blocked_without_token(self, tmp_path):
        """curl DELETE to git/refs is blocked without token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization(
            "curl -X DELETE https://api.github.com/repos/owner/repo/git/refs/heads/feature",
            token_dir=tmp_path,
        )
        assert result is not None
        assert "approval" in result.lower() or "AskUserQuestion" in result

    def test_curl_patch_main_blocked_without_token(self, tmp_path):
        """curl PATCH to main is blocked without token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization(
            "curl -X PATCH https://api.github.com/repos/owner/repo/git/refs/heads/main",
            token_dir=tmp_path,
        )
        assert result is not None
        assert "approval" in result.lower() or "AskUserQuestion" in result

    def test_gh_api_delete_ref_allowed_with_token(self, tmp_path):
        """gh api DELETE to git/refs is allowed with valid authorization token."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "branch-delete", "branch": "feature"},
                    token_dir=tmp_path)

        result = check_merge_authorization(
            "gh api -X DELETE repos/owner/repo/git/refs/heads/feature",
            token_dir=tmp_path,
        )
        assert result is None  # Allowed

    def test_gh_api_patch_main_allowed_with_token(self, tmp_path):
        """gh api PATCH to main is allowed with valid authorization token."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "force-push", "target_ref": "main"},
                    token_dir=tmp_path)

        result = check_merge_authorization(
            "gh api -X PATCH repos/owner/repo/git/refs/heads/main -f sha=abc",
            token_dir=tmp_path,
        )
        assert result is None  # Allowed

    def test_curl_patch_ref_allowed_with_token(self, tmp_path):
        """curl PATCH to git/refs is allowed with valid token."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "force-push", "target_ref": "feature"},
                    token_dir=tmp_path)

        result = check_merge_authorization(
            "curl -X PATCH https://api.github.com/repos/owner/repo/git/refs/heads/feature",
            token_dir=tmp_path,
        )
        assert result is None  # Allowed

    def test_gh_api_get_ref_not_blocked(self, tmp_path):
        """gh api GET to git/refs does not require a token (safe operation)."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization(
            "gh api repos/owner/repo/git/refs/heads/main",
            token_dir=tmp_path,
        )
        assert result is None  # Not blocked


# =============================================================================
# gh api implicit POST detection (-f, -F, --field, --raw-field, --input)
# =============================================================================


class TestGhApiImplicitPost:
    """Tests for gh api implicit POST detection via body parameter flags.

    gh api defaults to POST when body params (-f, -F, --field, --raw-field,
    --input) are present without an explicit -X/--method flag. These tests
    verify that such commands targeting git/refs or merge endpoints are detected.
    """

    # --- Dangerous: implicit POST to git/refs ---

    def test_gh_api_f_flag_git_refs(self):
        """gh api with -f flag targeting git/refs is dangerous (implicit POST)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/main -f sha=abc123 -f force=true"
        )

    def test_gh_api_F_flag_git_refs(self):
        """gh api with -F flag targeting git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/main -F sha=abc123"
        )

    def test_gh_api_field_flag_git_refs(self):
        """gh api with --field flag targeting git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/main --field sha=abc123"
        )

    def test_gh_api_raw_field_flag_git_refs(self):
        """gh api with --raw-field flag targeting git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/main --raw-field sha=abc123"
        )

    def test_gh_api_input_flag_git_refs(self):
        """gh api with --input flag targeting git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/main --input body.json"
        )

    def test_gh_api_f_flag_generic_git_refs(self):
        """gh api with -f flag to bare git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/git/refs -f ref=refs/heads/new -f sha=abc123"
        )

    # --- Dangerous: implicit POST to merge endpoint ---

    def test_gh_api_f_flag_merge(self):
        """gh api with -f flag targeting merge endpoint is dangerous (implicit POST)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/pulls/42/merge -f merge_method=squash"
        )

    def test_gh_api_input_flag_merge(self):
        """gh api with --input flag targeting merge endpoint is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/pulls/42/merge --input merge-body.json"
        )

    # --- Safe: explicit GET overrides implicit POST ---

    def test_gh_api_explicit_get_overrides_f_flag(self):
        """gh api -X GET with -f flag is safe (explicit GET overrides implicit POST)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/main -X GET -f sha=abc"
        )

    def test_gh_api_method_get_overrides_f_flag(self):
        """gh api --method GET with -f flag is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/main --method GET -f sha=abc"
        )

    def test_gh_api_explicit_get_overrides_merge_f_flag(self):
        """gh api -X GET with -f flag targeting merge is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/pulls/42/merge -X GET -f merge_method=squash"
        )

    # --- Safe: no body param flags (plain GET) ---

    def test_gh_api_no_flags_git_refs_is_safe(self):
        """gh api to git/refs without body params or method is safe (GET)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/git/refs/heads/main"
        )

    def test_gh_api_jq_flag_is_safe(self):
        """gh api with --jq flag (output filter, not body param) is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/git/refs --jq '.[0].ref'"
        )

    # --- Global flag variants ---

    def test_gh_api_implicit_post_with_repo_flag(self):
        """gh --repo with implicit POST to git/refs is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo api repos/owner/repo/git/refs/heads/main -f sha=abc"
        )

    def test_gh_api_implicit_post_with_R_flag(self):
        """gh -R with implicit POST to git/refs is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh -R owner/repo api repos/owner/repo/git/refs/heads/main -f sha=abc"
        )

    # --- Case insensitivity ---

    def test_gh_api_implicit_post_case_insensitive(self):
        """gh api implicit POST detection is case-insensitive for path."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api repos/owner/repo/Git/Refs/heads/main -f sha=abc"
        )


# =============================================================================
# curl implicit POST detection (-d, --data, --data-raw, --data-binary)
# =============================================================================


class TestCurlImplicitPost:
    """Tests for curl implicit POST detection via data flags.

    curl defaults to POST when -d/--data/--data-raw/--data-binary flags are
    present without an explicit -X/--request flag. These tests verify detection
    for git/refs and merge API endpoints.
    """

    # --- Dangerous: implicit POST to git/refs ---

    def test_curl_d_flag_git_refs(self):
        """curl -d with git/refs URL is dangerous (implicit POST)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl -d \'{"sha":"abc","force":true}\' https://api.github.com/repos/o/r/git/refs/heads/main'
        )

    def test_curl_data_flag_git_refs(self):
        """curl --data with git/refs URL is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl --data \'{"sha":"abc"}\' https://api.github.com/repos/o/r/git/refs/heads/main'
        )

    def test_curl_data_raw_flag_git_refs(self):
        """curl --data-raw with git/refs URL is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl --data-raw \'{"sha":"abc"}\' https://api.github.com/repos/o/r/git/refs/heads/main'
        )

    def test_curl_data_binary_flag_git_refs(self):
        """curl --data-binary with git/refs URL is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl --data-binary @body.json https://api.github.com/repos/o/r/git/refs/heads/main"
        )

    # --- Dangerous: implicit POST to merge endpoint ---

    def test_curl_d_flag_merge(self):
        """curl -d with merge URL is dangerous (implicit POST)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl -d \'{"merge_method":"squash"}\' https://api.github.com/repos/o/r/pulls/42/merge'
        )

    def test_curl_data_flag_merge(self):
        """curl --data with merge URL is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl --data \'{"merge_method":"squash"}\' https://api.github.com/repos/o/r/pulls/42/merge'
        )

    # --- Safe: explicit GET overrides implicit POST ---

    def test_curl_explicit_get_overrides_d_flag(self):
        """curl -X GET with -d flag is safe (explicit GET overrides)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl -X GET -d '' https://api.github.com/repos/o/r/git/refs"
        )

    def test_curl_request_get_overrides_data_flag(self):
        """curl --request GET with --data flag is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl --request GET --data '' https://api.github.com/repos/o/r/git/refs"
        )

    # --- Safe: no data flags (plain GET) ---

    def test_curl_no_flags_git_refs_is_safe(self):
        """curl to git/refs without data flags is safe (GET)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl https://api.github.com/repos/o/r/git/refs/heads/main"
        )

    # --- Case insensitivity ---

    def test_curl_implicit_post_case_insensitive(self):
        """curl implicit POST detection is case-insensitive for path."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl -d \'{"sha":"abc"}\' https://api.github.com/repos/o/r/Git/Refs/heads/main'
        )


# =============================================================================
# Contents API detection (write operations to /contents/ on main/master)
# =============================================================================


class TestContentsAPI:
    """Tests for Contents API write operation detection.

    The Contents API allows creating/updating/deleting files via PUT/PATCH/POST
    to /contents/ endpoints. These tests verify detection when targeting
    main or master branches.
    """

    # --- gh api: dangerous writes to contents on main/master ---

    def test_gh_api_put_contents_main(self):
        """gh api -X PUT to /contents/ with main branch is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X PUT repos/owner/repo/contents/README.md -f branch=main -f sha=abc"
        )

    def test_gh_api_put_contents_master(self):
        """gh api -X PUT to /contents/ with master branch is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X PUT repos/owner/repo/contents/README.md -f branch=master"
        )

    def test_gh_api_patch_contents_main(self):
        """gh api -X PATCH to /contents/ with main branch is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X PATCH repos/owner/repo/contents/README.md -f branch=main"
        )

    def test_gh_api_post_contents_main(self):
        """gh api -X POST to /contents/ with main branch is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X POST repos/owner/repo/contents/src/app.py -f branch=main"
        )

    def test_gh_api_method_put_contents_main(self):
        """gh api --method PUT to /contents/ with main is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api --method PUT repos/owner/repo/contents/README.md -f branch=main"
        )

    def test_gh_api_put_contents_main_case_insensitive(self):
        """Contents API detection is case-insensitive."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X PUT repos/owner/repo/Contents/README.md -f branch=Main"
        )

    def test_gh_api_put_contents_main_with_repo_flag(self):
        """gh --repo with PUT to /contents/ main is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo api -X PUT repos/owner/repo/contents/README.md -f branch=main"
        )

    # --- curl: dangerous writes to contents on main/master ---

    def test_curl_put_contents_main(self):
        """curl -X PUT to /contents/ API with main is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl -X PUT https://api.github.com/repos/o/r/contents/README.md -d \'{"branch":"main","sha":"abc"}\''
        )

    def test_curl_put_contents_master(self):
        """curl -X PUT to /contents/ API with master is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl -X PUT https://api.github.com/repos/o/r/contents/README.md -d \'{"branch":"master"}\''
        )

    def test_curl_request_put_contents_main(self):
        """curl --request PUT to /contents/ with main is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl --request PUT https://api.github.com/repos/o/r/contents/README.md -d '{\"branch\":\"main\"}'"
        )

    # --- Safe: GET operations on contents ---

    def test_gh_api_get_contents_main_is_safe(self):
        """gh api to /contents/ without mutating method is safe (read file)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api repos/owner/repo/contents/README.md"
        )

    def test_curl_get_contents_main_is_safe(self):
        """curl to /contents/ without mutating method is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl https://api.github.com/repos/o/r/contents/README.md"
        )

    # --- Safe: writes to contents on feature branches ---

    def test_gh_api_put_contents_feature_is_safe(self):
        """gh api PUT to /contents/ on a feature branch is safe (no main/master)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api -X PUT repos/owner/repo/contents/README.md -f branch=feature"
        )

    def test_curl_put_contents_feature_is_safe(self):
        """curl PUT to /contents/ on a feature branch is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl -X PUT https://api.github.com/repos/o/r/contents/README.md -d '{\"branch\":\"feature\"}'"
        )


# =============================================================================
# wget and httpie detection (alternative HTTP clients)
# =============================================================================


class TestAlternativeHttpClients:
    """Tests for wget (gated) and httpie (ungated by design) handling.

    Both clients can perform the same API operations as curl/gh api. wget uses
    the --method= flag and IS gated (it is in the mint classifier's _is_api_form).
    httpie (positional method arg) is WHOLLY ungated — out of charter, accepted
    under-block (#1077) — see the httpie tripwire section below.
    """

    # --- wget: dangerous operations ---

    def test_wget_delete_git_refs(self):
        """wget --method=DELETE to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "wget --method=DELETE https://api.github.com/repos/o/r/git/refs/heads/feature"
        )

    def test_wget_patch_git_refs(self):
        """wget --method=PATCH to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "wget --method=PATCH https://api.github.com/repos/o/r/git/refs/heads/feature"
        )

    def test_wget_post_git_refs(self):
        """wget --method=POST to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "wget --method=POST https://api.github.com/repos/o/r/git/refs"
        )

    def test_wget_put_git_refs(self):
        """wget --method=PUT to git/refs is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "wget --method=PUT https://api.github.com/repos/o/r/git/refs/heads/feature"
        )

    def test_wget_delete_merge(self):
        """wget --method=DELETE to merge endpoint is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "wget --method=DELETE https://api.github.com/repos/o/r/pulls/42/merge"
        )

    def test_wget_patch_merge(self):
        """wget --method=PATCH to merge endpoint is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "wget --method=PATCH https://api.github.com/repos/o/r/pulls/42/merge"
        )

    def test_wget_case_insensitive(self):
        """wget --method= detection is case-insensitive."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "wget --method=delete https://api.github.com/repos/o/r/git/refs/heads/feature"
        )

    # --- wget: safe operations ---

    def test_wget_get_git_refs_is_safe(self):
        """wget to git/refs without --method is safe (default GET)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "wget https://api.github.com/repos/o/r/git/refs"
        )

    def test_wget_get_merge_is_safe(self):
        """wget to merge endpoint without --method is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "wget https://api.github.com/repos/o/r/pulls/42/merge"
        )

    # --- httpie (http command): ungated BY DESIGN — accepted under-block tripwires ---
    #
    # httpie (`http`/`https` CLI) is WHOLLY out of charter (#1077, #1079-consistent):
    # its two read-floor arms (git/refs + merge) were REMOVED because the mint
    # classifier covers gh-api/curl/wget only — an httpie read arm gates a form the
    # mint cannot bind = a gated-but-unmintable over-block (a PERMANENT faithful-click
    # block). These pins assert ungated ON PURPOSE, mirroring
    # TestAcceptedRecognitionLimitationPins: if one flips RED, httpie was re-gated
    # without mint coverage. Do NOT re-gate.

    def test_http_delete_git_refs(self):
        """httpie DELETE to git/refs is ungated BY DESIGN — do NOT re-gate; a read
        arm without mint coverage is a gated-but-unmintable over-block."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "http DELETE api.github.com/repos/o/r/git/refs/heads/feature"
        )

    def test_http_patch_git_refs(self):
        """httpie PATCH to git/refs is ungated BY DESIGN — do NOT re-gate; a read
        arm without mint coverage is a gated-but-unmintable over-block."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "http PATCH api.github.com/repos/o/r/git/refs/heads/feature"
        )

    def test_http_post_git_refs(self):
        """httpie POST to git/refs is ungated BY DESIGN — do NOT re-gate; a read
        arm without mint coverage is a gated-but-unmintable over-block."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "http POST api.github.com/repos/o/r/git/refs"
        )

    def test_http_put_git_refs(self):
        """httpie PUT to git/refs is ungated BY DESIGN — do NOT re-gate; a read
        arm without mint coverage is a gated-but-unmintable over-block."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "http PUT api.github.com/repos/o/r/git/refs/heads/feature"
        )

    def test_http_delete_merge(self):
        """httpie DELETE to a merge endpoint is ungated BY DESIGN — do NOT re-gate;
        a read arm without mint coverage is a gated-but-unmintable over-block."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "http DELETE api.github.com/repos/o/r/pulls/42/merge"
        )

    def test_http_patch_merge(self):
        """httpie PATCH to a merge endpoint is ungated BY DESIGN — do NOT re-gate;
        a read arm without mint coverage is a gated-but-unmintable over-block."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "http PATCH api.github.com/repos/o/r/pulls/42/merge"
        )

    def test_http_with_auth_flags(self):
        """httpie with auth flags before the method is ungated BY DESIGN — do NOT
        re-gate; a read arm without mint coverage is a gated-but-unmintable
        over-block."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "http -a user:pass DELETE api.github.com/repos/o/r/git/refs/heads/feature"
        )

    def test_http_case_insensitive(self):
        """httpie with a lowercase method is ungated BY DESIGN — do NOT re-gate; a
        read arm without mint coverage is a gated-but-unmintable over-block."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "http delete api.github.com/repos/o/r/git/refs/heads/feature"
        )

    def test_https_command_delete_git_refs(self):
        """https (httpie alias) DELETE to git/refs is ungated BY DESIGN — do NOT
        re-gate; a read arm without mint coverage is a gated-but-unmintable
        over-block."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "https DELETE api.github.com/repos/o/r/git/refs/heads/feature"
        )

    # --- httpie: safe operations ---

    def test_http_get_git_refs_is_safe(self):
        """http GET to git/refs is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "http GET api.github.com/repos/o/r/git/refs"
        )

    def test_http_get_merge_is_safe(self):
        """http GET to merge endpoint is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "http GET api.github.com/repos/o/r/pulls/42/merge"
        )


# =============================================================================
# Tool-specific flag exclusion tests (document intentional flag specificity)
# =============================================================================


class TestToolSpecificFlagExclusion:
    """Tests documenting that patterns correctly distinguish tool-specific flags.

    gh api uses -X/--method (not --request).
    curl uses -X/--request (not --method).
    Using the wrong tool's flag should not be detected — these tests
    document that the flag specificity is intentional, not accidental.
    """

    def test_gh_api_request_flag_not_detected(self):
        """gh api --request DELETE is NOT detected (gh uses --method, not --request)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api --request DELETE repos/owner/repo/git/refs/heads/feature"
        )

    def test_curl_method_flag_not_detected(self):
        """curl --method DELETE is NOT detected (curl uses --request, not --method)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl --method DELETE https://api.github.com/repos/owner/repo/git/refs/heads/feature"
        )

    def test_gh_api_request_flag_merge_not_detected(self):
        """gh api --request PUT to merge is NOT detected."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh api --request PUT repos/owner/repo/pulls/42/merge"
        )

    def test_curl_method_flag_merge_not_detected(self):
        """curl --method PUT to merge is NOT detected."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "curl --method PUT https://api.github.com/repos/owner/repo/pulls/42/merge"
        )


# =============================================================================
# Variable indirection bypass documentation
# =============================================================================


class TestVariableIndirectionBypass:
    """Tests documenting that variable indirection bypasses regex detection.

    This is an inherent limitation of command-line regex matching — when
    the URL path is stored in a variable and expanded at runtime, the
    regex cannot see the actual path. These tests document the limitation.
    """

    def test_variable_url_bypasses_detection(self):
        """Variable indirection: URL in variable is NOT detected (inherent regex limit)."""
        from merge_guard_pre import is_dangerous_command

        # The URL is in a variable — regex sees $URL, not the actual path
        assert not is_dangerous_command(
            'URL="repos/owner/repo/git/refs/heads/main" && gh api -X DELETE $URL'
        )

    def test_variable_endpoint_bypasses_detection(self):
        """Variable endpoint: endpoint in variable is NOT detected."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'ENDPOINT="git/refs/heads/main" && gh api -X PATCH repos/owner/repo/$ENDPOINT'
        )


# =============================================================================
# Heredoc-to-shell tests for API patterns
# =============================================================================


class TestAPIHeredocToShell:
    """Tests that API commands inside heredocs fed to shells are detected."""

    def test_heredoc_gh_api_delete_ref(self):
        """bash << EOF with gh api -X DELETE git/refs is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "bash << EOF\ngh api -X DELETE repos/o/r/git/refs/heads/main\nEOF"
        )

    def test_heredoc_gh_api_patch_ref(self):
        """bash << EOF with gh api -X PATCH git/refs is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "bash << EOF\ngh api -X PATCH repos/o/r/git/refs/heads/main -f sha=abc\nEOF"
        )

    def test_heredoc_curl_delete_ref(self):
        """bash << EOF with curl -X DELETE git/refs is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "bash << EOF\ncurl -X DELETE https://api.github.com/repos/o/r/git/refs/heads/main\nEOF"
        )


# =============================================================================
# Multi-break line continuation tests for API patterns
# =============================================================================


class TestAPIMultiBreakLineContinuation:
    """Tests for multi-break line continuations in API commands.

    Commands split across multiple lines with backslash-newline are
    normalized before pattern matching. These tests verify that
    commands with 2+ breaks are correctly detected.
    """

    def test_gh_api_delete_multi_break(self):
        """gh api -X DELETE with multiple line breaks is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api \\\n-X DELETE \\\nrepos/o/r/git/refs/heads/feature"
        )

    def test_gh_api_patch_main_multi_break(self):
        """gh api -X PATCH to main with multiple line breaks is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api \\\n-X PATCH \\\nrepos/o/r/git/refs/heads/main \\\n-f sha=abc"
        )

    def test_curl_delete_multi_break(self):
        """curl -X DELETE with multiple line breaks is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl \\\n-X DELETE \\\nhttps://api.github.com/repos/o/r/git/refs/heads/feature"
        )

    def test_curl_patch_multi_break_with_auth(self):
        """curl with auth header and multiple line breaks is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'curl \\\n-H "Authorization: token ghp_xxx" \\\n-X PATCH \\\nhttps://api.github.com/repos/o/r/git/refs/heads/main'
        )


# =============================================================================
# curl --request merge pattern fix (M2: previously only matched -X)
# =============================================================================


class TestCurlRequestMergeFix:
    """Tests that curl --request (long form) is now detected for merge patterns.

    Previously the curl merge pattern only matched -X, not --request.
    The remediation added --request support. These tests verify the fix.
    """

    def test_curl_request_put_merge(self):
        """curl --request PUT to merge endpoint is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl --request PUT https://api.github.com/repos/o/r/pulls/42/merge"
        )

    def test_curl_request_patch_merge(self):
        """curl --request PATCH to merge endpoint is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl --request PATCH https://api.github.com/repos/o/r/pulls/42/merge"
        )

    def test_curl_request_post_merge(self):
        """curl --request POST to merge endpoint is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl --request POST https://api.github.com/repos/o/r/pulls/42/merge"
        )

    def test_curl_X_merge_still_works(self):
        """curl -X PUT to merge still works (regression check)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl -X PUT https://api.github.com/repos/o/r/pulls/42/merge"
        )

    def test_curl_request_merge_case_insensitive(self):
        """curl --request detection is case-insensitive."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "curl --request put https://api.github.com/repos/o/r/pulls/42/merge"
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

        write_token({"operation_type": "push-to-main", "target_ref": "main"},
                    token_dir=tmp_path)

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
        """A typed merge token matches a command with the same PR number."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "merge", "pr_number": "42"}}
        assert _token_matches_command(token, "gh pr merge 42")

    def test_token_with_mismatched_pr_number(self):
        """A typed merge token does NOT match a different PR number (positive op,
        mismatched target -> REFUSE)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "merge", "pr_number": "42"}}
        assert not _token_matches_command(token, "gh pr merge 99")

    def test_token_with_matching_branch(self):
        """A typed branch-delete token matches a branch -D command."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "branch-delete", "branch": "old-feature"}}
        assert _token_matches_command(token, "git branch -D old-feature")

    def test_token_with_mismatched_branch(self):
        """A typed branch-delete token does NOT match a different branch."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "branch-delete", "branch": "old-feature"}}
        assert not _token_matches_command(token, "git branch -D other-branch")

    def test_token_with_branch_delete_force(self):
        """A typed branch-delete token matches a --delete --force command."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "branch-delete", "branch": "cleanup"}}
        assert _token_matches_command(token, "git branch --delete --force cleanup")

    def test_empty_context_denies_all(self):
        """FAIL-CLOSED (was wildcard-allow): an empty context has no
        operation_type, so it authorizes NOTHING."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {}}
        assert not _token_matches_command(token, "gh pr merge 42")
        assert not _token_matches_command(token, "git branch -D anything")

    def test_malformed_context_denies(self):
        """FAIL-CLOSED (F-READ-1, was graceful-allow): a non-dict context proves
        nothing -> REFUSE."""
        from merge_guard_pre import _token_matches_command

        token = {"context": "not a dict"}
        assert not _token_matches_command(token, "gh pr merge 42")

    def test_missing_context_key_denies(self):
        """FAIL-CLOSED (was allow-through): a token with no context key has no
        operation_type -> REFUSE."""
        from merge_guard_pre import _token_matches_command

        token = {}
        assert not _token_matches_command(token, "gh pr merge 42")

    def test_untyped_pr_context_token_denies_any_command(self):
        """#1032 CLOSURE (was allow-through, A1/A2 read-floor): an UNTYPED token
        (pr_number present but NO operation_type) authorizes NOTHING. The old
        read fell open for op_type=None (skipped the typed cross-op guard, then
        terminal-allowed); the fix denies on the op-type axis. Op-LESS so it is
        genuinely C2-coupled (revert C2 -> the untyped token authorizes -> RED)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"pr_number": "42"}}
        assert not _token_matches_command(token, "git push --force origin main")

    def test_untyped_branch_context_token_denies_any_command(self):
        """#1032 CLOSURE (was allow-through, A1/A2 read-floor): an UNTYPED token
        (branch present but NO operation_type) authorizes NOTHING. Op-LESS so it
        is C2-coupled (revert C2 -> the untyped token authorizes -> RED)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"branch": "old"}}
        assert not _token_matches_command(token, "gh pr merge 42")

    # ── HALT #29 branch-delete multi-target: deny + single-target no-regression
    #    controls (architect addendum). The #30 fix refuses a multi-target
    #    `git branch -D a b` (extra unapproved branch); these controls guard
    #    against it over-correcting into a #1031 over-block on the legit single
    #    case. Mirrors the force-push target-axis pair. ──
    def test_branch_delete_multi_target_denied(self):
        """A single-branch token must NOT authorize a MULTI-target delete (the
        command also removes an UNAPPROVED branch). Revert-coupled to the #30
        _extract_branch_name multi-target fix (revert #30 -> RED)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "branch-delete", "branch": "a"}}
        assert not _token_matches_command(token, "git branch -D a b")
        assert not _token_matches_command(token, "git branch --delete --force a b")

    def test_branch_delete_single_target_still_authorizes(self):
        """NO-REGRESSION control: the #30 multi-target fix must NOT over-block the
        legit SINGLE-target delete — a token for 'a' still authorizes both the
        `-D` and the `--delete --force` single-branch forms."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "branch-delete", "branch": "a"}}
        assert _token_matches_command(token, "git branch -D a")
        assert _token_matches_command(token, "git branch --delete --force a")

    def test_branch_delete_api_ref_single_target_still_authorizes(self):
        """NO-REGRESSION control (API-ref-DELETE single form): a branch-delete
        token still authorizes the single-ref `gh api -X DELETE .../git/refs/
        heads/<ref>` form (the API-ref parser is unaffected by the CLI
        multi-target positional count)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "branch-delete", "branch": "a"}}
        assert _token_matches_command(
            token, "gh api -X DELETE repos/o/r/git/refs/heads/a"
        )

    def test_mismatched_token_blocks_in_check_merge_authorization(self, tmp_path):
        """check_merge_authorization blocks when a typed token's target doesn't
        match the command (and does NOT consume the non-matching token)."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        # Token scoped to PR #42 (typed + targeted so write_token accepts it).
        write_token({"operation_type": "merge", "pr_number": "42"},
                    token_dir=tmp_path)

        # Attempt to merge PR #99 — should be blocked.
        result = check_merge_authorization("gh pr merge 99", token_dir=tmp_path)
        assert result is not None
        assert "does not match" in result

        # Token should NOT be consumed (it belongs to PR #42).
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1


# =============================================================================
# Session scoping (pact_context session_id)
# =============================================================================


class TestSessionScoping:
    """Tests for session-scoped token isolation."""

    def test_token_includes_session_id(self, tmp_path):
        """Token file includes session_id when session context is set."""
        from merge_guard_post import write_token

        with patch("merge_guard_post.get_session_id", return_value="session-abc"):
            result = write_token({"test": True, "operation_type": "merge"}, token_dir=tmp_path)

        with open(result) as f:
            data = json.load(f)
        assert data["session_id"] == "session-abc"

    def test_token_omits_session_id_when_not_set(self, tmp_path):
        """Token file omits session_id when session context is not available."""
        from merge_guard_post import write_token

        with patch("merge_guard_post.get_session_id", return_value=""):
            result = write_token({"test": True, "operation_type": "merge"}, token_dir=tmp_path)

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

        with patch("merge_guard_pre.get_session_id", return_value="session-abc"):
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

        with patch("merge_guard_pre.get_session_id", return_value="session-abc"):
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        # Token NOT cleaned up — it may be valid for its own session
        assert (tmp_path / "merge-authorized-22222").exists()

    def test_no_session_id_accepts_any_token(self, tmp_path):
        """When no session ID is available, any valid token is accepted."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
            "session_id": "session-xyz",
        }
        (tmp_path / "merge-authorized-33333").write_text(json.dumps(token_data))

        with patch("merge_guard_pre.get_session_id", return_value=""):
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is not None

    def test_token_without_session_REJECTED_when_current_session_known(self, tmp_path):
        """SEC-S1 cycle-2 inversion: token without session_id is REJECTED
        when the current session is known.

        Pre-cycle-2 (cycle-1 short-circuit predicate
        `if current_session and token_session and current_session != token_session`):
        the AND-short-circuit on `not token_session` left tokens with empty
        session_id through — the bypass surface SEC-S1 closes.

        Cycle-2 revised asymmetric predicate at merge_guard_pre.py:635:
        `if current_session: if not token_session or current_session != token_session: continue`
        — when current_session IS populated, both an empty token_session AND
        a foreign token_session reject. Graceful-degradation preserved at
        :5068 (test_no_session_id_accepts_any_token): when current_session
        is empty, any valid token still accepted.

        See architect §3 (asymmetric predicate design) + Task #33 HANDOFF
        for the design rationale.

        # counter-test: remove the inner `if not token_session` half of the
        #               predicate at merge_guard_pre.py:645 → empty token_
        #               session falls through to acceptance (the cycle-1
        #               attack surface re-opens); assertion `result is None`
        #               FAILS because result is a valid token dict.
        # expected RED cardinality: {1}
        """
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
        }
        (tmp_path / "merge-authorized-44444").write_text(json.dumps(token_data))

        with patch("merge_guard_pre.get_session_id", return_value="session-abc"):
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is None

    def test_sec_s1_rejects_empty_token_session_when_current_session_known(
        self, tmp_path
    ):
        """SEC-S1 cycle-2: explicit empty-token_session rejection.

        Parallel to test_token_without_session_REJECTED_when_current_session_known
        but uses an explicit empty `session_id` field (vs. the missing field
        in :5086). Pins the same fail-CLOSED behavior for both shapes:
          - {"session_id": ""}   (this test)
          - {} (no session_id)   (:5086)
        Both are bypass-surface attacker shapes under the cycle-1 predicate;
        both reject under cycle-2.

        # counter-test: revert merge_guard_pre.py:635-646 to the cycle-1
        #               short-circuit predicate
        #                   `if current_session and token_session and
        #                    current_session != token_session: continue`
        #               → empty token_session AND-short-circuits to NOT
        #               continue → token accepted; assertion FAILS.
        # NOTE: removing ONLY the inner `if not token_session` half is NOT
        # load-bearing for this fixture — `current_session != ""` is also
        # True so the surviving half still rejects. The cycle-1 short-
        # circuit revert is the discriminative mutation (verified during
        # TEST-phase cp-revert-test-restore sampling).
        # expected RED cardinality: {1}
        """
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
            "session_id": "",  # Explicit empty string (attacker-written shape)
        }
        (tmp_path / "merge-authorized-55555").write_text(json.dumps(token_data))

        with patch("merge_guard_pre.get_session_id", return_value="real-session-abc"):
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        # Token NOT cleaned up — it may be valid for some other session
        # context (preserves the cycle-1 don't-cleanup-foreign convention)
        assert (tmp_path / "merge-authorized-55555").exists()

    def test_sec_s1_rejects_foreign_token_session_when_current_session_known(
        self, tmp_path
    ):
        """SEC-S1 cycle-2: foreign-session rejection under populated current.

        This is the cycle-1 behavior preserved verbatim (foreign session
        always rejected) but anchored under the new asymmetric predicate
        framing. Documents that the AND-tightening at
        merge_guard_pre.py:645 (`if not token_session OR current_session
        != token_session`) preserves the foreign-session rejection half
        exactly while ADDING the empty-token_session rejection half.

        # counter-test: remove the inner `current_session != token_session`
        #               half of the predicate at merge_guard_pre.py:645 →
        #               foreign-session token would fall through to
        #               acceptance; assertion FAILS.
        # expected RED cardinality: {1}
        """
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
            "session_id": "other-session-B",
        }
        (tmp_path / "merge-authorized-66666").write_text(json.dumps(token_data))

        with patch("merge_guard_pre.get_session_id", return_value="real-session-A"):
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        # Token NOT cleaned up — it may be valid for its own session
        assert (tmp_path / "merge-authorized-66666").exists()


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
        # Issue #658: fail-closed deny must include hookEventName.
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

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
        """When both primary and fallback filenames exist, returns None.

        Layer 5 (I-1) retires unused tokens before O_EXCL, so under
        normal use the primary O_EXCL cannot collide with a pre-existing
        unused token. This test pins the double-collision returns-None
        behavior independently of Layer 5 by stubbing cleanup so the
        collision survives to O_EXCL (mirroring a transient cleanup
        failure or a concurrent same-microsecond race).
        """
        import merge_guard_post
        from merge_guard_post import write_token

        now = time.time()
        timestamp = int(now)
        ms_suffix = int(now * 1000) % 1000

        # Pre-create both the primary and fallback files
        primary = tmp_path / f"merge-authorized-{timestamp}"
        fallback = tmp_path / f"merge-authorized-{timestamp}-{ms_suffix}"
        primary.write_text("taken")
        fallback.write_text("taken")

        with patch.object(merge_guard_post, "_cleanup_unused_tokens", lambda _td: None), \
             patch("merge_guard_post.time") as mock_time:
            mock_time.time.return_value = now
            result = write_token({"test": True, "operation_type": "merge"}, token_dir=tmp_path)

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
            result = write_token({"test": True, "operation_type": "merge"}, token_dir=tmp_path)

        # Should return None because the primary write failed and the retry
        # path (FileExistsError) is not triggered (different exception type)
        assert result is None

    def test_tool_response_as_boolean_true(self, tmp_path):
        """tool_response as boolean True — non-dict, exits early, no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Merge?"}]},
            "tool_response": True,
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("merge_guard_post.write_token") as mock_write_token:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # "True" does not match affirmative patterns
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0
        # Cause-of-no-token: non-dict tool_response triggered isinstance early-exit
        # before the merge-question branch could reach write_token.
        mock_write_token.assert_not_called()

    def test_tool_response_as_boolean_false(self, tmp_path):
        """tool_response as boolean False — non-dict, exits early, no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Merge?"}]},
            "tool_response": False,
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("merge_guard_post.write_token") as mock_write_token:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0
        # Cause-of-no-token: non-dict tool_response triggered isinstance early-exit
        # before the merge-question branch could reach write_token.
        mock_write_token.assert_not_called()


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
            "tool_response": {"answers": {"Should I merge?": "yes"}},
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
            "tool_response": {"answers": {"q": "yes"}},
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
            "tool_response": {"answers": {"q": "yes"}},
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
            "tool_response": {"answers": {"q": "yes"}},
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
            "tool_response": {"answers": {"Should I merge?": "yes"}},
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
            "tool_response": {"answers": {"q": "yes"}},
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
            "tool_response": {"answers": {"q": "yes"}},
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
            "tool_response": {"answers": {"q": "yes"}},
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
            "tool_response": {"answers": {"q": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0


class TestAnswerExtractionEdgeCases:
    """Tests for isinstance guards on the answers dict extraction path.

    The fix extracts answer from tool_response["answers"] dict using
    next(iter(values())). Every malformed input must result in
    answer="" which prevents token creation (fail-closed).
    """

    def test_answers_is_list_not_dict(self, tmp_path):
        """answers is a list instead of dict — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_response": {"answers": ["yes"]},
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
            "tool_response": {"answers": "yes"},
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
            "tool_response": {"answers": 1},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_answers_is_none(self, tmp_path):
        """answers is None inside tool_response dict — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_response": {"answers": None},
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
            "tool_response": {"answers": {}},
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
            "tool_response": {"answers": {"Should I merge?": 42}},
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
            "tool_response": {"answers": {"Should I merge?": True}},
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
            "tool_response": {"answers": {"Should I merge?": None}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_formatted_string_rejected_as_non_dict(self, tmp_path):
        """tool_response is the formatted string from AskUserQuestion.

        When tool_response is a string like 'User has answered your questions:
        "Confirm merge?"="yes"', it is rejected as non-dict — no token.
        """
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Confirm merge of PR #252?"}]},
            "tool_response": 'User has answered your questions: "Confirm merge of PR #252?"="Yes, merge".',
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # Non-dict tool_response rejected entirely — no token
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
                        {"label": "Yes, merge", "description": "Run `gh pr merge 252`"},
                        {"label": "Cancel", "description": "Abort the merge"},
                    ],
                    "multiSelect": False,
                }]
            },
            "tool_response": {
                "questions": [{
                    "question": "Confirm merge of PR #252 to main?",
                    "header": "Merge",
                    "options": [
                        {"label": "Yes, merge", "description": "Run `gh pr merge 252`"},
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

        # Pre hook: consume token MAX_USES times (terminal rename happens
        # on the final slot under #720 Bug C).
        from shared.merge_guard_common import MAX_USES, USE_MARKER_SUFFIX

        for _ in range(MAX_USES):
            # #1042: execution flags must match the approval (`gh pr merge 252`,
            # no privileged flags). --squash is unbound; --delete-branch (a bound
            # merge side-effect) would now correctly mismatch an unflagged approval.
            pre_input = json.dumps({
                "tool_input": {"command": "gh pr merge 252 --squash"}
            })
            with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
                 patch("sys.stdin", io.StringIO(pre_input)):
                with pytest.raises(SystemExit) as exc_info:
                    pre_main()
            assert exc_info.value.code == 0

        # Token should be terminally consumed after MAX_USES uses.
        active_tokens = [
            p for p in tmp_path.glob("merge-authorized-*")
            if not p.name.endswith(".consumed")
            and USE_MARKER_SUFFIX not in p.name
        ]
        assert len(active_tokens) == 0
        consumed_tokens = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(consumed_tokens) == 1

    def test_force_push_approval_flow(self, tmp_path):
        """Full flow for force push approval."""
        from merge_guard_post import main as post_main
        from merge_guard_pre import main as pre_main

        post_input = json.dumps({
            "tool_input": {
                "questions": [{
                    "question": "Force push to main? This will overwrite remote history.",
                    "options": [
                        {"label": "Yes, force-push", "description": "Run `git push --force origin main`"},
                        {"label": "Cancel", "description": "Abort"},
                    ],
                }]
            },
            "tool_response": {
                "answers": {
                    "Force push to main? This will overwrite remote history.": "Yes, force-push",
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
                    "question": "Delete the old feature branch?",
                    "options": [
                        {"label": "Yes, delete", "description": "Run `git branch -D feat/old-feature`"},
                        {"label": "Cancel", "description": "Abort"},
                    ],
                }]
            },
            "tool_response": {
                "answers": {
                    "Delete the old feature branch?": "Yes, delete",
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
            "tool_response": {
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
        """Token includes session_id when session context is available."""
        from merge_guard_post import main as post_main

        post_input = json.dumps({
            "tool_input": {
                "questions": [{
                    "question": "Merge the PR?",
                    "options": [
                        {"label": "Yes, merge", "description": "Run `gh pr merge 42`"},
                        {"label": "Cancel", "description": "Abort"},
                    ],
                }]
            },
            "tool_response": {
                "answers": {"Merge the PR?": "Yes, merge"}
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(post_input)), \
             patch("merge_guard_post.get_session_id", return_value="test-session-123"):
            with pytest.raises(SystemExit):
                post_main()

        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1
        token_data = json.loads(tokens[0].read_text())
        assert token_data["session_id"] == "test-session-123"

    def test_multi_question_mints_from_clicked_option(self, tmp_path):
        """KD-12 + #32 option-anchoring: with multiple questions, the mint keys
        each answer to its SPECIFIC question and mints from the CLICKED option's
        command. The command-bearing option mints; the unrelated affirmative adds
        no second pair. A single distinct (op,target) → one token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {
                "questions": [
                    {"question": "Merge the PR?", "options": [
                        {"label": "Yes, merge", "description": "Run `gh pr merge 42`"},
                        {"label": "Cancel", "description": "Abort"},
                    ]},
                    {"question": "Also update the changelog?", "options": [
                        {"label": "Yes", "description": "Update it"},
                        {"label": "No", "description": "Skip"},
                    ]},
                ]
            },
            "tool_response": {
                "answers": {
                    "Merge the PR?": "Yes, merge",
                    "Also update the changelog?": "Yes",
                },
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit):
                main()

        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1
        assert json.loads(tokens[0].read_text())["context"]["pr_number"] == "42"

    def test_multi_question_command_in_question_prose_only_refuses(self, tmp_path):
        """#32 F-REVIEW-1: when the command is in QUESTION PROSE only (the clicked
        option carries NO command), the mint REFUSES — the operator clicked a
        generic option, never the command. This is the option-anchoring closure
        (sibling of the mints-from-clicked-option case above)."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {
                "questions": [
                    {"question": "Should I run `gh pr merge 42`?", "options": [
                        {"label": "Yes, proceed", "description": "go ahead"},
                        {"label": "Cancel", "description": "Abort"},
                    ]},
                    {"question": "Also update the changelog?", "options": [
                        {"label": "Yes", "description": "Update it"},
                    ]},
                ]
            },
            "tool_response": {
                "answers": {
                    "Should I run `gh pr merge 42`?": "Yes, proceed",
                    "Also update the changelog?": "Yes",
                },
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit):
                main()

        assert list(tmp_path.glob("merge-authorized-*")) == []

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
            "tool_response": {
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
            "tool_response": {
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

    def test_question_key_mismatch_does_not_fall_back(self, tmp_path):
        """KD-12 KILLED the next(iter(answers.values())) fallback: when the
        answers key does not EXACTLY match the question text (e.g. a trailing
        space), that question has NO answer — there is no fallback to the first
        dict value — so NO token mints, even though the question embeds a command.
        """
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {
                "questions": [{"question": "Run `gh pr merge 42`?"}]
            },
            "tool_response": {
                "answers": {
                    "Run `gh pr merge 42`? ": "yes",  # trailing space — key mismatch
                },
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit):
                main()

        # No fallback: the question's answer is absent (key mismatch) → no mint.
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0


# =============================================================================
# Process substitution bypass prevention
# =============================================================================


class TestProcessSubstitutionBypass:
    """Verify that process substitution fed to a shell interpreter is NOT
    stripped and remains detectable as dangerous.

    ``bash <(echo 'gh pr merge 42')`` executes the echo output via the
    shell, so echo content must not be stripped.
    """

    def test_bash_process_sub_is_dangerous(self):
        """bash <(echo 'dangerous') is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("bash <(echo 'gh pr merge 42')")

    def test_sh_process_sub_is_dangerous(self):
        """sh <(printf 'dangerous') is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("sh <(printf 'gh pr merge 42')")

    def test_zsh_process_sub_is_dangerous(self):
        """zsh <(echo 'dangerous') is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("zsh <(echo 'gh pr merge 42')")

    def test_bash_process_sub_double_quoted(self):
        """bash <(echo "dangerous") with double quotes is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('bash <(echo "gh pr merge 42")')

    def test_non_shell_process_sub_still_stripped(self):
        """cat <(echo 'dangerous') is NOT a shell — still stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("cat <(echo 'gh pr merge 42')")

    def test_grep_process_sub_still_stripped(self):
        """grep <(echo 'dangerous') is NOT a shell — still stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("grep <(echo 'gh pr merge 42')")

    def test_strip_helper_process_sub(self):
        """_strip_non_executable_content preserves echo args when process sub to shell."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content("bash <(echo 'gh pr merge 42')")
        assert "gh pr merge" in result


# =============================================================================
# xargs piping bypass prevention
# =============================================================================


class TestXargsBypass:
    """Verify that echo/printf piped to xargs + shell interpreter is NOT
    stripped and remains detectable as dangerous.

    ``echo "gh pr merge 42" | xargs bash`` passes the echo output as
    arguments to bash, which executes them.
    """

    def test_echo_xargs_bash_is_dangerous(self):
        """echo piped to xargs bash is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('echo "gh pr merge 42" | xargs bash')

    def test_echo_xargs_sh_is_dangerous(self):
        """echo piped to xargs sh is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('echo "gh pr merge 42" | xargs sh')

    def test_echo_xargs_zsh_is_dangerous(self):
        """echo piped to xargs zsh is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('echo "gh pr merge 42" | xargs zsh')

    def test_echo_xargs_with_flags_bash(self):
        """echo piped to xargs with flags to bash is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'echo "gh pr merge 42" | xargs -I {} bash -c {}'
        )

    def test_printf_xargs_bash_is_dangerous(self):
        """printf piped to xargs bash is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('printf "gh pr merge 42" | xargs bash')

    def test_echo_xargs_grep_still_stripped(self):
        """echo piped to xargs grep is NOT a shell — still stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('echo "gh pr merge 42" | xargs grep')

    def test_strip_helper_xargs(self):
        """_strip_non_executable_content preserves echo args when piped to xargs shell."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content(
            'echo "gh pr merge 42" | xargs bash'
        )
        assert "gh pr merge" in result


# =============================================================================
# Here-string false positive prevention
# =============================================================================


class TestHereStringStripping:
    """Verify that here-strings (<<<) are stripped to prevent false positives,
    with guards for shell interpreters and command substitution."""

    def test_cat_herestring_single_quoted_not_dangerous(self):
        """cat <<< 'dangerous' is not a command — stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("cat <<< 'gh pr merge 42'")

    def test_cat_herestring_double_quoted_not_dangerous(self):
        """cat <<< "dangerous" is not a command — stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('cat <<< "gh pr merge 42"')

    def test_grep_herestring_not_dangerous(self):
        """grep <<< 'dangerous' is not a command — stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("grep <<< 'gh pr merge 42'")

    def test_bash_herestring_single_quoted_is_dangerous(self):
        """bash <<< 'dangerous' IS executed — preserved."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("bash <<< 'gh pr merge 42'")

    def test_bash_herestring_double_quoted_is_dangerous(self):
        """bash <<< "dangerous" IS executed — preserved."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('bash <<< "gh pr merge 42"')

    def test_sh_herestring_is_dangerous(self):
        """sh <<< 'dangerous' IS executed — preserved."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("sh <<< 'gh pr merge 42'")

    def test_zsh_herestring_is_dangerous(self):
        """zsh <<< 'dangerous' IS executed — preserved."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("zsh <<< 'gh pr merge 42'")

    def test_herestring_cmd_substitution_is_dangerous(self):
        """cat <<< "$(dangerous)" has command substitution — preserved."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('cat <<< "$(gh pr merge 42)"')

    def test_herestring_backtick_substitution_is_dangerous(self):
        """cat <<< "`dangerous`" has backtick substitution — preserved."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('cat <<< "`gh pr merge 42`"')

    def test_strip_helper_herestring_single(self):
        """_strip_non_executable_content strips single-quoted here-string."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content("cat <<< 'gh pr merge 42'")
        assert "gh pr merge" not in result

    def test_strip_helper_herestring_double(self):
        """_strip_non_executable_content strips double-quoted here-string."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content('cat <<< "gh pr merge 42"')
        assert "gh pr merge" not in result

    def test_strip_helper_herestring_bash_preserved(self):
        """_strip_non_executable_content preserves bash here-string content."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content("bash <<< 'gh pr merge 42'")
        assert "gh pr merge" in result

    def test_herestring_force_push_not_dangerous(self):
        """Here-string with force push text is not dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('cat <<< "git push --force origin main"')

    def test_herestring_branch_delete_not_dangerous(self):
        """Here-string with branch delete text is not dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("cat <<< 'git branch -D feat/old'")


# =============================================================================
# Idempotent Token Consumption — comprehensive tests
# =============================================================================


class TestIdempotentTokenConsumption:
    """Comprehensive tests for the rename-to-.consumed idempotent consumption mechanism.

    Covers: _consume_token(), _cleanup_consumed_tokens() (both hooks),
    find_valid_token() skipping .consumed files, check_merge_authorization()
    integration with _consume_token(), and edge cases.
    """

    # -------------------------------------------------------------------------
    # _consume_token() unit tests
    # -------------------------------------------------------------------------

    def test_consume_token_successful_rename(self, tmp_path):
        """_consume_token renames the file to .consumed and returns True."""
        from merge_guard_pre import _consume_token

        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text('{"test": true}')

        result = _consume_token(str(token_file))

        assert result is True
        assert not token_file.exists()
        assert (tmp_path / "merge-authorized-99999.consumed").exists()

    def test_consume_token_idempotent_when_already_consumed(self, tmp_path):
        """_consume_token returns True when .consumed file already exists (concurrent invocation)."""
        from merge_guard_pre import _consume_token

        token_path = str(tmp_path / "merge-authorized-99999")
        # Simulate prior consumption: only .consumed exists
        consumed_file = tmp_path / "merge-authorized-99999.consumed"
        consumed_file.write_text('{"test": true}')

        result = _consume_token(token_path)

        assert result is True

    def test_consume_token_returns_false_when_genuinely_lost(self, tmp_path):
        """_consume_token returns False when original is gone AND no .consumed exists."""
        from merge_guard_pre import _consume_token

        # Neither the original nor .consumed exists
        token_path = str(tmp_path / "merge-authorized-nonexistent")

        result = _consume_token(token_path)

        assert result is False

    def test_consume_token_fails_closed_on_unexpected_oserror(self, tmp_path):
        """_consume_token returns False on non-ENOENT OSError (fail-closed)."""
        from merge_guard_pre import _consume_token

        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text('{"test": true}')

        # Mock os.rename to raise a PermissionError (which is an OSError subclass)
        with patch("merge_guard_pre.os.rename", side_effect=PermissionError("Permission denied")):
            result = _consume_token(str(token_file))

        assert result is False

    def test_consume_token_fails_closed_on_generic_oserror(self, tmp_path):
        """_consume_token returns False on generic OSError (e.g., I/O error)."""
        from merge_guard_pre import _consume_token

        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text('{"test": true}')

        with patch("merge_guard_pre.os.rename", side_effect=OSError(5, "I/O error")):
            result = _consume_token(str(token_file))

        assert result is False

    def test_consume_token_preserves_consumed_file_content(self, tmp_path):
        """_consume_token preserves the token data in the .consumed file."""
        from merge_guard_pre import _consume_token

        token_file = tmp_path / "merge-authorized-99999"
        original_content = '{"pr": "42", "expires_at": 999999}'
        token_file.write_text(original_content)

        _consume_token(str(token_file))

        consumed_file = tmp_path / "merge-authorized-99999.consumed"
        assert consumed_file.read_text() == original_content

    def test_consume_token_consumed_file_permissions(self, tmp_path):
        """Consumed file inherits permissions from original (os.rename preserves)."""
        from merge_guard_pre import _consume_token

        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text('{"test": true}')
        os.chmod(str(token_file), 0o600)

        _consume_token(str(token_file))

        consumed_file = tmp_path / "merge-authorized-99999.consumed"
        mode = stat.S_IMODE(os.stat(str(consumed_file)).st_mode)
        assert mode == 0o600

    # -------------------------------------------------------------------------
    # _cleanup_consumed_tokens() tests (pre-hook version)
    # -------------------------------------------------------------------------

    def test_cleanup_removes_expired_consumed_tokens(self, tmp_path):
        """_cleanup_consumed_tokens removes .consumed files older than TOKEN_TTL."""
        from merge_guard_pre import _cleanup_consumed_tokens, TOKEN_TTL

        # Create a stale .consumed file (mtime far in the past)
        stale = tmp_path / "merge-authorized-00001.consumed"
        stale.write_text('{}')
        old_mtime = time.time() - (2 * TOKEN_TTL)  # older than TTL
        os.utime(str(stale), (old_mtime, old_mtime))

        _cleanup_consumed_tokens(tmp_path)

        assert not stale.exists()

    def test_cleanup_preserves_fresh_consumed_tokens(self, tmp_path):
        """_cleanup_consumed_tokens preserves .consumed files within TOKEN_TTL."""
        from merge_guard_pre import _cleanup_consumed_tokens

        fresh = tmp_path / "merge-authorized-00002.consumed"
        fresh.write_text('{}')
        # mtime is now (fresh) — should not be cleaned up

        _cleanup_consumed_tokens(tmp_path)

        assert fresh.exists()

    def test_cleanup_handles_empty_directory(self, tmp_path):
        """_cleanup_consumed_tokens handles empty directory without error."""
        from merge_guard_pre import _cleanup_consumed_tokens

        _cleanup_consumed_tokens(tmp_path)  # Should not raise

    def test_cleanup_only_removes_consumed_not_active_tokens(self, tmp_path):
        """_cleanup_consumed_tokens does not remove active token files."""
        from merge_guard_pre import _cleanup_consumed_tokens

        active = tmp_path / "merge-authorized-00003"
        active.write_text('{"active": true}')

        _cleanup_consumed_tokens(tmp_path)

        assert active.exists()

    def test_cleanup_handles_concurrent_deletion(self, tmp_path):
        """_cleanup_consumed_tokens handles file deleted by concurrent cleanup."""
        from merge_guard_pre import _cleanup_consumed_tokens, TOKEN_TTL

        stale = tmp_path / "merge-authorized-00001.consumed"
        stale.write_text('{}')
        old_mtime = time.time() - (2 * TOKEN_TTL)
        os.utime(str(stale), (old_mtime, old_mtime))

        # Mock os.path.getmtime to raise FileNotFoundError (concurrent deletion)
        # Target the shared module where cleanup_consumed_tokens now lives
        with patch("shared.merge_guard_common.os.path.getmtime", side_effect=FileNotFoundError):
            _cleanup_consumed_tokens(tmp_path)  # Should not raise

    def test_cleanup_mixed_expired_and_fresh(self, tmp_path):
        """_cleanup_consumed_tokens removes only expired files from a mixed set."""
        from merge_guard_pre import _cleanup_consumed_tokens, TOKEN_TTL

        # Stale consumed token
        stale = tmp_path / "merge-authorized-00001.consumed"
        stale.write_text('{}')
        old_mtime = time.time() - (2 * TOKEN_TTL)
        os.utime(str(stale), (old_mtime, old_mtime))

        # Fresh consumed token
        fresh = tmp_path / "merge-authorized-00002.consumed"
        fresh.write_text('{}')

        _cleanup_consumed_tokens(tmp_path)

        assert not stale.exists()
        assert fresh.exists()

    # -------------------------------------------------------------------------
    # Post-hook _cleanup_consumed_tokens() tests
    # -------------------------------------------------------------------------

    def test_post_hook_cleanup_removes_expired_consumed(self, tmp_path):
        """Post-hook _cleanup_consumed_tokens removes stale .consumed files."""
        from merge_guard_post import _cleanup_consumed_tokens, TOKEN_TTL

        stale = tmp_path / "merge-authorized-00001.consumed"
        stale.write_text('{}')
        old_mtime = time.time() - (2 * TOKEN_TTL)
        os.utime(str(stale), (old_mtime, old_mtime))

        _cleanup_consumed_tokens(tmp_path)

        assert not stale.exists()

    def test_post_hook_cleanup_preserves_fresh_consumed(self, tmp_path):
        """Post-hook _cleanup_consumed_tokens preserves fresh .consumed files."""
        from merge_guard_post import _cleanup_consumed_tokens

        fresh = tmp_path / "merge-authorized-00002.consumed"
        fresh.write_text('{}')

        _cleanup_consumed_tokens(tmp_path)

        assert fresh.exists()

    # -------------------------------------------------------------------------
    # write_token() triggers cleanup
    # -------------------------------------------------------------------------

    def test_write_token_cleans_up_stale_consumed(self, tmp_path):
        """write_token() cleans up stale .consumed files during token creation."""
        from merge_guard_post import write_token, TOKEN_TTL

        # Create a stale .consumed file
        stale = tmp_path / "merge-authorized-00001.consumed"
        stale.write_text('{}')
        old_mtime = time.time() - (2 * TOKEN_TTL)
        os.utime(str(stale), (old_mtime, old_mtime))

        # Create a new token — should clean up stale consumed files
        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)

        assert token_path is not None
        assert not stale.exists()  # Stale consumed cleaned up

    def test_write_token_preserves_fresh_consumed(self, tmp_path):
        """write_token() preserves fresh .consumed files during token creation."""
        from merge_guard_post import write_token

        # Create a fresh .consumed file
        fresh = tmp_path / "merge-authorized-00001.consumed"
        fresh.write_text('{}')

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)

        assert token_path is not None
        assert fresh.exists()  # Fresh consumed preserved

    # -------------------------------------------------------------------------
    # find_valid_token() skips .consumed files
    # -------------------------------------------------------------------------

    def test_find_valid_token_skips_consumed_files(self, tmp_path):
        """find_valid_token() ignores .consumed files when scanning for tokens."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        # Create a .consumed file with valid data
        consumed = tmp_path / "merge-authorized-11111.consumed"
        consumed.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
        }))

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None

    def test_find_valid_token_returns_active_ignores_consumed(self, tmp_path):
        """find_valid_token() returns active token when both active and consumed exist."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        # Consumed token
        consumed = tmp_path / "merge-authorized-11111.consumed"
        consumed.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"type": "consumed"},
        }))

        # Active token
        active = tmp_path / "merge-authorized-22222"
        active.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"type": "active"},
        }))

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is not None
        assert result["context"]["type"] == "active"
        assert "22222" in path
        assert not path.endswith(".consumed")

    def test_find_valid_token_calls_cleanup(self, tmp_path):
        """find_valid_token() triggers cleanup of stale consumed tokens."""
        from merge_guard_pre import find_valid_token, TOKEN_TTL

        # Create a stale consumed token
        stale = tmp_path / "merge-authorized-00001.consumed"
        stale.write_text('{}')
        old_mtime = time.time() - (2 * TOKEN_TTL)
        os.utime(str(stale), (old_mtime, old_mtime))

        find_valid_token(token_dir=tmp_path)

        assert not stale.exists()

    def test_find_valid_token_only_consumed_in_dir(self, tmp_path):
        """find_valid_token() returns None when only .consumed files exist."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        # Only consumed files
        for i in range(3):
            consumed = tmp_path / f"merge-authorized-{i:05d}.consumed"
            consumed.write_text(json.dumps({
                "created_at": now,
                "expires_at": now + 300,
                "context": {},
            }))

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None

    # -------------------------------------------------------------------------
    # check_merge_authorization() + _consume_token() integration
    # -------------------------------------------------------------------------

    def test_authorization_returns_error_on_consumption_failure(self, tmp_path):
        """check_merge_authorization() returns error when _consume_token fails."""
        from merge_guard_pre import check_merge_authorization

        now = time.time()
        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"operation_type": "merge", "pr_number": "42"},
        }))

        # Mock _consume_token to return False (unexpected failure). The token
        # MATCHES the command (typed + targeted), so the flow reaches the
        # consume step and surfaces the internal-error path.
        with patch("merge_guard_pre._consume_token", return_value=False):
            result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)

        assert result is not None
        assert "internal error" in result.lower()
        assert "AskUserQuestion" in result

    def test_authorization_allows_when_consumption_succeeds(self, tmp_path):
        """check_merge_authorization() returns None when _consume_token succeeds."""
        from merge_guard_pre import check_merge_authorization

        now = time.time()
        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"operation_type": "merge", "pr_number": "42"},
        }))

        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is None

    def test_authorization_consumes_and_blocks_after_budget_exhausted(self, tmp_path):
        """Full flow: MAX_USES commands authorized, next blocked (#720 Bug C)."""
        from shared.merge_guard_common import MAX_USES
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)
        assert token_path is not None

        # Burn through the full MAX_USES budget
        for _ in range(MAX_USES):
            assert check_merge_authorization("gh pr merge 42", token_dir=tmp_path) is None

        # Terminal rename after last slot
        assert Path(token_path + ".consumed").exists()

        # Next command: blocked, no active tokens
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "AskUserQuestion" in result

    def test_concurrent_authorization_both_succeed(self, tmp_path):
        """Two concurrent _consume_token calls for the same token both succeed
        by claiming distinct slots (#720 Bug C). The first claim is slot 1
        (token still on disk); the second claim is slot 2 (terminal rename
        fires)."""
        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)

        # First invocation: claims slot 1 (token still on disk; budget left)
        assert _consume_token(token_path) is True
        assert Path(token_path + USE_MARKER_SUFFIX + "1").exists()
        assert Path(token_path).exists()
        assert not Path(token_path + ".consumed").exists()

        # Second invocation: claims slot 2 (last slot → terminal rename)
        assert _consume_token(token_path) is True
        assert Path(token_path + USE_MARKER_SUFFIX + "2").exists()
        assert not Path(token_path).exists()
        assert Path(token_path + ".consumed").exists()

    # -------------------------------------------------------------------------
    # Edge cases
    # -------------------------------------------------------------------------

    def test_consumed_and_original_both_exist(self, tmp_path):
        """Edge case: both original and .consumed exist (shouldn't happen normally).

        _consume_token should still succeed by renaming (overwriting .consumed).
        """
        from merge_guard_pre import _consume_token

        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text('{"original": true}')
        consumed_file = tmp_path / "merge-authorized-99999.consumed"
        consumed_file.write_text('{"already_consumed": true}')

        result = _consume_token(str(token_file))

        assert result is True
        assert not token_file.exists()
        assert consumed_file.exists()
        # The rename overwrites the .consumed file with the original content
        assert json.loads(consumed_file.read_text())["original"] is True

    def test_empty_token_directory_no_consumed_files(self, tmp_path):
        """check_merge_authorization handles empty token directory gracefully."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "AskUserQuestion" in result

    def test_only_consumed_files_no_active_tokens(self, tmp_path):
        """check_merge_authorization blocks when only .consumed files remain."""
        from merge_guard_pre import check_merge_authorization

        now = time.time()
        consumed = tmp_path / "merge-authorized-99999.consumed"
        consumed.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
        }))

        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "AskUserQuestion" in result

    def test_consumed_file_with_secure_permissions(self, tmp_path):
        """Token created with write_token maintains 0o600 after MAX_USES consumes."""
        from shared.merge_guard_common import MAX_USES
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)
        assert token_path is not None

        # Verify original has secure permissions
        original_mode = stat.S_IMODE(os.stat(token_path).st_mode)
        assert original_mode == 0o600

        # Burn the full budget so the terminal rename fires
        for _ in range(MAX_USES):
            check_merge_authorization("gh pr merge 42", token_dir=tmp_path)

        # Verify .consumed file retains secure permissions
        consumed_path = token_path + ".consumed"
        assert Path(consumed_path).exists()
        consumed_mode = stat.S_IMODE(os.stat(consumed_path).st_mode)
        assert consumed_mode == 0o600

    # -------------------------------------------------------------------------
    # Full lifecycle integration
    # -------------------------------------------------------------------------

    def test_full_lifecycle_create_consume_cleanup(self, tmp_path):
        """Full lifecycle: create token -> consume MAX_USES times -> cleanup after TTL."""
        from shared.merge_guard_common import MAX_USES, TOKEN_TTL
        from merge_guard_post import write_token
        from merge_guard_pre import (
            _cleanup_consumed_tokens,
            check_merge_authorization,
        )

        # 1. Create token
        token_path = write_token({"operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)
        assert token_path is not None
        assert Path(token_path).exists()

        # 2. Consume via authorization MAX_USES times → terminal rename
        for _ in range(MAX_USES):
            assert check_merge_authorization(
                "gh pr merge 42", token_dir=tmp_path
            ) is None
        consumed_path = token_path + ".consumed"
        assert Path(consumed_path).exists()
        assert not Path(token_path).exists()

        # 3. Consumed file persists during TTL
        _cleanup_consumed_tokens(tmp_path)
        assert Path(consumed_path).exists()  # Still within TTL

        # 4. After TTL, consumed file is cleaned up (along with .use-N markers)
        old_mtime = time.time() - (2 * TOKEN_TTL)
        os.utime(consumed_path, (old_mtime, old_mtime))
        for marker in tmp_path.glob("merge-authorized-*.use-*"):
            os.utime(marker, (old_mtime, old_mtime))
        _cleanup_consumed_tokens(tmp_path)
        assert not Path(consumed_path).exists()

    def test_non_matching_token_not_consumed(self, tmp_path):
        """FAIL-CLOSED non-consumption: a token whose target does NOT match the
        command is blocked and PRESERVED (not consumed) — so it remains
        available for its real command. (Under the I-1 invariant there is one
        unused token at a time; the read side tests that token against the
        command rather than searching for a matching one, so a mismatch must
        leave the token intact.)"""
        from merge_guard_pre import check_merge_authorization

        now = time.time()
        # A token authorizing merge of PR #99 only.
        token = tmp_path / "merge-authorized-10001"
        token.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"operation_type": "merge", "pr_number": "99"},
        }))

        # Running a DIFFERENT PR's merge is blocked (target mismatch)...
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "does not match" in result.lower()

        # ...and the token is NOT consumed (preserved for PR #99).
        assert token.exists()
        assert list(tmp_path.glob("merge-authorized-*.consumed")) == []


# =============================================================================
# gh pr close: dangerous command detection
# =============================================================================


class TestGhPrCloseDetection:
    """Tests for gh pr close detection in merge guard pre-hook.

    Only 'gh pr close --delete-branch' is dangerous. Bare 'gh pr close'
    (without --delete-branch) is trivially reversible and ALLOWED.

    Covers: --delete-branch position variants, bare close (safe), flags,
    chained commands, false positive prevention, and edge cases.
    """

    # -------------------------------------------------------------------------
    # Bare gh pr close (no --delete-branch) is SAFE
    # -------------------------------------------------------------------------

    def test_gh_pr_close_bare_is_safe(self):
        """Bare 'gh pr close' without --delete-branch is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr close")

    def test_gh_pr_close_with_number_is_safe(self):
        """'gh pr close 123' without --delete-branch is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr close 123")

    def test_gh_pr_close_with_large_number_is_safe(self):
        """'gh pr close 99999' without --delete-branch is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr close 99999")

    def test_gh_pr_close_with_comment_flag_is_safe(self):
        """'gh pr close --comment' without --delete-branch is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr close 42 --comment 'closing as wontfix'")

    def test_gh_pr_close_with_repo_flag_is_safe(self):
        """'gh pr close --repo' without --delete-branch is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr close 42 --repo owner/repo")

    # -------------------------------------------------------------------------
    # gh pr close --delete-branch IS dangerous
    # -------------------------------------------------------------------------

    def test_gh_pr_close_delete_branch_after_number(self):
        """'gh pr close 42 --delete-branch' is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr close 42 --delete-branch")

    def test_gh_pr_close_delete_branch_before_number(self):
        """'gh pr close --delete-branch 42' is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr close --delete-branch 42")

    def test_gh_pr_close_delete_branch_no_number(self):
        """'gh pr close --delete-branch' (no PR number) is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr close --delete-branch")

    def test_gh_pr_close_delete_branch_with_comment(self):
        """'gh pr close --delete-branch --comment' is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr close 42 --delete-branch --comment 'done'")

    def test_gh_pr_close_delete_branch_parenthesized_comment_not_overblocked(self, tmp_path):
        """REGRESSION GUARD — the marquee faithful-click over-block: a close carrying a
        PARENTHESIZED comment value, `gh pr close 7 --comment "(done)" --delete-branch`,
        must be GATED (is_dangerous) yet classified as a SINGLE op (not >=2-compound) and
        AUTHORIZE end-to-end on a faithful click. The `(`/`)` inside the quoted comment
        are exactly what the now-removed metachar fail-closed suppressor / read
        deny-outright over-blocked; this pins the invariant that a faithful
        single-command click always mints and executes.

        NON-VACUITY: the end-to-end authorize is the load-bearing assertion — a
        re-introduced metachar suppressor / deny-outright would refuse this faithful
        click despite a valid token, flipping the final assert RED. The metachar-free
        sibling test_gh_pr_close_delete_branch_with_comment ('done') does NOT exercise
        the metachar path, so this guard is not redundant with it.
        """
        from merge_guard_pre import (
            is_dangerous_command,
            is_compound_destructive_command,
            check_merge_authorization,
        )
        from merge_guard_post import write_token
        from shared.merge_guard_common import extract_command_context

        cmd = 'gh pr close 7 --comment "(done)" --delete-branch'
        # Gated as a single destructive op — NOT refused-outright as >=2-compound.
        assert is_dangerous_command(cmd) is True
        assert is_compound_destructive_command(cmd) is False
        # Faithful click: the clicked option mints a token for this exact command and
        # the read side AUTHORIZES it (returns None) — no metachar over-block.
        ctx = extract_command_context(cmd)
        assert write_token(ctx, token_dir=tmp_path) is not None
        assert check_merge_authorization(cmd, token_dir=tmp_path) is None

    def test_gh_pr_close_delete_branch_with_repo(self):
        """'gh pr close --delete-branch --repo' is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr close 42 --delete-branch --repo owner/repo")

    # -------------------------------------------------------------------------
    # Whitespace and formatting edge cases
    # -------------------------------------------------------------------------

    def test_gh_pr_close_delete_branch_extra_whitespace(self):
        """'gh  pr  close' with extra whitespace + --delete-branch is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh  pr  close 42 --delete-branch")

    def test_gh_pr_close_delete_branch_tab_separated(self):
        """'gh pr close' with tabs + --delete-branch is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh\tpr\tclose 42 --delete-branch")

    def test_gh_pr_close_bare_extra_whitespace_safe(self):
        """'gh  pr  close' without --delete-branch is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh  pr  close 42")

    # -------------------------------------------------------------------------
    # Chained commands with --delete-branch
    # -------------------------------------------------------------------------

    def test_gh_pr_close_delete_branch_after_and(self):
        """'gh pr close --delete-branch' after && is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("cd /tmp && gh pr close 42 --delete-branch")

    def test_gh_pr_close_delete_branch_after_semicolon(self):
        """'gh pr close --delete-branch' after semicolon is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("echo done; gh pr close 42 --delete-branch")

    def test_gh_pr_close_delete_branch_after_or(self):
        """'gh pr close --delete-branch' after || is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("false || gh pr close 42 --delete-branch")

    def test_gh_pr_close_delete_branch_in_subshell(self):
        """'gh pr close --delete-branch' in $() subshell is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("$(gh pr close 42 --delete-branch)")

    def test_gh_pr_close_delete_branch_with_env_var(self):
        """'gh pr close --delete-branch' with env var prefix is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("GH_TOKEN=abc gh pr close 42 --delete-branch")

    def test_gh_pr_close_delete_branch_in_bash_c(self):
        """'gh pr close --delete-branch' inside bash -c is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("bash -c 'gh pr close 42 --delete-branch'")

    # -------------------------------------------------------------------------
    # Bare close in chained commands is SAFE
    # -------------------------------------------------------------------------

    def test_gh_pr_close_bare_after_and_safe(self):
        """Bare 'gh pr close' (no --delete-branch) after && is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("cd /tmp && gh pr close 42")

    def test_gh_pr_close_bare_after_semicolon_safe(self):
        """Bare 'gh pr close' after semicolon is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("echo done; gh pr close 42")

    # -------------------------------------------------------------------------
    # False positive prevention (stripping logic) with --delete-branch
    # -------------------------------------------------------------------------

    def test_echo_gh_pr_close_delete_branch_not_dangerous(self):
        """echo with 'gh pr close --delete-branch' text is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('echo "gh pr close 42 --delete-branch"')

    def test_echo_single_quoted_gh_pr_close_delete_branch_not_dangerous(self):
        """echo with single-quoted 'gh pr close --delete-branch' is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("echo 'gh pr close 42 --delete-branch'")

    def test_comment_gh_pr_close_delete_branch_not_dangerous(self):
        """Commented-out 'gh pr close --delete-branch' is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("# gh pr close 42 --delete-branch")

    def test_variable_assignment_gh_pr_close_delete_branch_not_dangerous(self):
        """Variable assignment containing 'gh pr close --delete-branch' is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('CMD="gh pr close 42 --delete-branch"')

    def test_heredoc_gh_pr_close_delete_branch_not_dangerous(self):
        """'gh pr close --delete-branch' inside heredoc is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        cmd = "cat << 'EOF'\ngh pr close 42 --delete-branch\nEOF"
        assert not is_dangerous_command(cmd)

    def test_git_commit_msg_gh_pr_close_delete_branch_not_dangerous(self):
        """'gh pr close --delete-branch' in commit message is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('git commit -m "gh pr close 42 --delete-branch"')

    def test_echo_push_main_not_dangerous_still(self):
        """Ensure other false positive prevention still works alongside new pattern."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('echo "git push origin main"')

    # -------------------------------------------------------------------------
    # Bypass vector prevention with --delete-branch
    # -------------------------------------------------------------------------

    def test_pipe_to_bash_gh_pr_close_delete_branch_detected(self):
        """echo piped to bash with 'gh pr close --delete-branch' IS dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("echo 'gh pr close 42 --delete-branch' | bash")

    def test_eval_var_gh_pr_close_delete_branch_detected(self):
        """eval with variable containing 'gh pr close --delete-branch' IS dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('CMD="gh pr close 42 --delete-branch" && eval $CMD')

    def test_bare_var_expansion_gh_pr_close_delete_branch_detected(self):
        """Bare variable expansion with 'gh pr close --delete-branch' IS dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('CMD="gh pr close 42 --delete-branch" && $CMD')

    # -------------------------------------------------------------------------
    # Negative tests: similar but safe commands
    # -------------------------------------------------------------------------

    def test_gh_pr_list_safe(self):
        """'gh pr list' is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr list")

    def test_gh_pr_view_safe(self):
        """'gh pr view' is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr view 42")

    def test_gh_pr_create_safe(self):
        """'gh pr create' is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr create --title 'test'")

    def test_gh_pr_checkout_safe(self):
        """'gh pr checkout' is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr checkout 42")

    def test_gh_pr_review_safe(self):
        """'gh pr review' is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr review 42 --approve")

    def test_gh_issue_close_safe(self):
        """'gh issue close' is NOT dangerous (intentionally excluded per #265)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh issue close 42")

    def test_close_as_substring_safe(self):
        """Words containing 'close' as substring are NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("echo 'disclosure notice'")


# =============================================================================
# gh pr close: merge_guard_post keyword detection
# =============================================================================


class TestGhPrClosePostHook:
    """Tests for gh pr close keyword detection in merge_guard_post.

    Ensures the post-hook creates authorization tokens when
    AskUserQuestion text mentions closing PRs.
    """

    def test_close_pr_command_detected_not_prose(self):
        """KD-9 command-driven: a `gh pr close` command fires the hint; bare
        'close PR' prose without a command does NOT."""
        from merge_guard_post import is_merge_question

        assert is_merge_question("Should I run `gh pr close 42`?")
        assert not is_merge_question("Should I close PR #42?")

    def test_close_pull_request_prose_alone_not_detected(self):
        """'close pull request' prose with no command is NOT detected."""
        from merge_guard_post import is_merge_question

        assert not is_merge_question("Should I close pull request 42?")

    def test_pr_close_prose_alone_not_detected(self):
        """'PR close' prose with no command is NOT detected."""
        from merge_guard_post import is_merge_question

        assert not is_merge_question("PR close requested for #42")

    def test_gh_pr_close_command_detected(self):
        """'gh pr close <N>' command triggers detection."""
        from merge_guard_post import is_merge_question

        assert is_merge_question("Run gh pr close 42?")

    def test_close_pr_prose_not_detected_any_case(self):
        """Bare 'Close PR' prose is NOT detected regardless of case (the command,
        not the keyword, is the signal)."""
        from merge_guard_post import is_merge_question

        assert not is_merge_question("Close PR #42 now?")
        assert not is_merge_question("CLOSE PR #42?")

    def test_close_without_pr_not_detected(self):
        """Bare 'close' without PR context is NOT a merge question."""
        from merge_guard_post import is_merge_question

        assert not is_merge_question("Should I close the file handle?")

    def test_close_issue_not_detected(self):
        """'close issue' is NOT a merge question (intentionally excluded)."""
        from merge_guard_post import is_merge_question

        assert not is_merge_question("Should I close issue #42?")


# =============================================================================
# gh pr close: token context matching
# =============================================================================


class TestGhPrCloseTokenMatching:
    """Tests for _token_matches_command with gh pr close commands."""

    def test_token_matches_gh_pr_close_same_pr(self):
        """Token with PR number matches gh pr close for same PR."""
        from merge_guard_pre import _token_matches_command

        # GAP2: a faithful close --delete-branch token binds --delete-branch, so it
        # set-matches the --delete-branch exec (bare-close→delete escalation is the
        # NEW escalation-denied counter-test below).
        token = {"context": {"pr_number": "42", "operation_type": "close", "bound_flags": ["--delete-branch"]}}
        assert _token_matches_command(token, "gh pr close 42 --delete-branch")

    def test_token_rejects_gh_pr_close_different_pr(self):
        """Token with PR number rejects gh pr close for different PR."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"pr_number": "42", "operation_type": "close"}}
        assert not _token_matches_command(token, "gh pr close 99 --delete-branch")

    def test_token_no_context_denies_gh_pr_close(self):
        """FAIL-CLOSED (was ambiguous-permissive): an empty context has no
        operation_type → it authorizes no gh pr close."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {}}
        assert not _token_matches_command(token, "gh pr close 42 --delete-branch")

    def test_token_branch_context_denies_gh_pr_close(self):
        """FAIL-CLOSED (was ambiguous-permissive): a branch-only context (no
        operation_type) does NOT authorize a gh pr close."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"branch": "feat/old"}}
        assert not _token_matches_command(token, "gh pr close 42 --delete-branch")

    def test_close_token_rejects_merge_command(self):
        """Close token does NOT authorize merge (operation_type mismatch)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"pr_number": "42", "operation_type": "close"}}
        assert not _token_matches_command(token, "gh pr merge 42")

    def test_merge_token_rejects_close_command(self):
        """Merge token does NOT authorize close (operation_type mismatch)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"pr_number": "42", "operation_type": "merge"}}
        assert not _token_matches_command(token, "gh pr close 42 --delete-branch")

    def test_token_without_operation_type_denies_any(self):
        """FAIL-CLOSED (was backward-compat allow-any): an untyped token
        (operation_type absent) authorizes NOTHING — no close, no merge."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"pr_number": "42"}}
        assert not _token_matches_command(token, "gh pr close 42 --delete-branch")
        assert not _token_matches_command(token, "gh pr merge 42")


# =============================================================================
# gh pr close: full authorization flow
# =============================================================================


class TestGhPrCloseAuthorization:
    """Integration tests for gh pr close --delete-branch through check_merge_authorization."""

    def test_gh_pr_close_delete_branch_blocked_without_token(self, tmp_path):
        """gh pr close --delete-branch is blocked when no token exists."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert result is not None
        assert "approval" in result.lower()

    def test_gh_pr_close_bare_allowed_without_token(self, tmp_path):
        """Bare gh pr close (no --delete-branch) is allowed without token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("gh pr close 42", token_dir=tmp_path)
        assert result is None

    def test_gh_pr_close_delete_branch_allowed_with_valid_token(self, tmp_path):
        """gh pr close --delete-branch allowed when valid token exists."""
        import time

        from merge_guard_pre import check_merge_authorization

        now = time.time()
        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            # GAP2: a FAITHFUL close --delete-branch mint binds --delete-branch, so it
            # set-matches the --delete-branch exec → authorizes.
            "context": {"pr_number": "42", "operation_type": "close", "bound_flags": ["--delete-branch"]},
        }))

        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert result is None

    def test_bare_close_token_does_not_authorize_delete_branch_escalation(self, tmp_path):
        """GAP2 escalation-denied: a bare-close token (bound_flags=[]) does NOT
        authorize `gh pr close N --delete-branch`. PR + op MATCH, so the deny is
        attributable SOLELY to the unbound --delete-branch (not a PR/op mismatch).
        Non-vacuity: revert the GAP2 bind (drop --delete-branch from
        PRIVILEGED_FLAGS['close']) → both bound_flags become [] → match → flips to allow."""
        import time

        from merge_guard_pre import check_merge_authorization

        now = time.time()
        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            # MATCHING pr+op, NO --delete-branch bound → the bare-close token.
            "context": {"pr_number": "42", "operation_type": "close", "bound_flags": []},
        }))

        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert result is not None  # DENIED — a bare-close token cannot authorize the --delete-branch escalation

    def test_gh_pr_close_delete_branch_blocked_with_mismatched_pr(self, tmp_path):
        """gh pr close --delete-branch blocked with different PR number."""
        import time

        from merge_guard_pre import check_merge_authorization

        now = time.time()
        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"pr_number": "99", "operation_type": "close"},
        }))

        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert result is not None
        assert "does not match" in result.lower()

    def test_gh_pr_close_delete_branch_blocked_with_expired_token(self, tmp_path):
        """gh pr close --delete-branch blocked when token is expired."""
        import time

        from merge_guard_pre import check_merge_authorization

        now = time.time()
        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text(json.dumps({
            "created_at": now - 600,
            "expires_at": now - 300,
            "context": {"pr_number": "42", "operation_type": "close"},
        }))

        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert result is not None

    def test_gh_pr_close_delete_branch_consumes_token(self, tmp_path):
        """gh pr close --delete-branch consumes token (single-use)."""
        import time

        from merge_guard_pre import check_merge_authorization

        now = time.time()
        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"operation_type": "close", "pr_number": "42", "bound_flags": ["--delete-branch"]},
        }))

        # First call — allowed and consumes token
        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert result is None

        # Second call — blocked (token consumed)
        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert result is not None

    def test_error_message_mentions_close(self, tmp_path):
        """Error message includes 'close' in the list of guarded operations."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert "close" in result.lower()

    def test_merge_token_cannot_authorize_close(self, tmp_path):
        """A merge token cannot authorize a close --delete-branch."""
        import time

        from merge_guard_pre import check_merge_authorization

        now = time.time()
        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"pr_number": "42", "operation_type": "merge"},
        }))

        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert result is not None
        assert "does not match" in result.lower()

    def test_close_token_cannot_authorize_merge(self, tmp_path):
        """A close token cannot authorize a merge."""
        import time

        from merge_guard_pre import check_merge_authorization

        now = time.time()
        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"pr_number": "42", "operation_type": "close"},
        }))

        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "does not match" in result.lower()


# =============================================================================
# gh pr close: adversarial bypass vector tests (TEST phase)
# =============================================================================


class TestGhPrCloseBypassVectors:
    """Adversarial tests for gh pr close --delete-branch — execution-via-indirection.

    Only tests with --delete-branch should be dangerous. Bare close bypass
    vectors are now safe (bare close is allowed).
    """

    # --- Process substitution bypass ---

    def test_bash_process_sub_gh_pr_close_delete_branch(self):
        """bash <(echo 'gh pr close 42 --delete-branch') executes — detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("bash <(echo 'gh pr close 42 --delete-branch')")

    def test_sh_process_sub_gh_pr_close_delete_branch(self):
        """sh <(printf 'gh pr close 42 --delete-branch') executes — detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("sh <(printf 'gh pr close 42 --delete-branch')")

    def test_bash_process_sub_bare_close_safe(self):
        """bash <(echo 'gh pr close 42') without --delete-branch — safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("bash <(echo 'gh pr close 42')")

    # --- xargs bypass ---

    def test_echo_xargs_bash_gh_pr_close_delete_branch(self):
        """echo piped to xargs bash with gh pr close --delete-branch — detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('echo "gh pr close 42 --delete-branch" | xargs bash')

    def test_echo_xargs_bash_bare_close_safe(self):
        """echo piped to xargs bash with bare gh pr close — safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('echo "gh pr close 42" | xargs bash')

    # --- Here-string bypass ---

    def test_bash_herestring_sq_gh_pr_close_delete_branch(self):
        """bash <<< 'gh pr close 42 --delete-branch' IS executed — detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("bash <<< 'gh pr close 42 --delete-branch'")

    def test_bash_herestring_bare_close_safe(self):
        """bash <<< 'gh pr close 42' without --delete-branch — safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("bash <<< 'gh pr close 42'")

    # --- Heredoc to shell bypass ---

    def test_heredoc_to_bash_gh_pr_close_delete_branch(self):
        """bash << EOF with gh pr close --delete-branch — detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("bash << EOF\ngh pr close 42 --delete-branch\nEOF")

    def test_heredoc_to_bash_bare_close_safe(self):
        """bash << EOF with bare gh pr close — safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("bash << EOF\ngh pr close 42\nEOF")

    # --- Command substitution ---

    def test_cmd_sub_gh_pr_close_delete_branch(self):
        """echo "$(gh pr close 42 --delete-branch)" — detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('echo "$(gh pr close 42 --delete-branch)"')

    def test_cmd_sub_bare_close_safe(self):
        """echo "$(gh pr close 42)" without --delete-branch — safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('echo "$(gh pr close 42)"')

    # --- Pipe to shell variants ---

    def test_echo_piped_to_sh_gh_pr_close_delete_branch(self):
        """echo piped to sh with gh pr close --delete-branch — detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("echo 'gh pr close 42 --delete-branch' | sh")

    def test_echo_piped_to_sh_bare_close_safe(self):
        """echo piped to sh with bare gh pr close — safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("echo 'gh pr close 42' | sh")

    # --- eval/source variants ---

    def test_var_eval_gh_pr_close_delete_branch(self):
        """Variable + eval with gh pr close --delete-branch — detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('CMD="gh pr close 42 --delete-branch"; eval $CMD')

    def test_var_eval_bare_close_safe(self):
        """Variable + eval with bare gh pr close — safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('CMD="gh pr close 42"; eval $CMD')


# =============================================================================
# gh pr close: additional edge cases (TEST phase)
# =============================================================================


class TestGhPrCloseEdgeCases:
    """Edge cases and boundary conditions for gh pr close detection.

    With the --delete-branch requirement, bare close edge cases are now safe.
    """

    def test_gh_pr_close_delete_branch_multiple_in_chain(self):
        """Multiple gh pr close --delete-branch in a chain — dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr close 1 --delete-branch && gh pr close 2 --delete-branch")

    def test_gh_pr_close_bare_multiple_in_chain_safe(self):
        """Multiple bare gh pr close in a chain — safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr close 1 && gh pr close 2")

    def test_gh_pr_close_mixed_with_merge(self):
        """Bare gh pr close alongside gh pr merge — merge makes it dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr close 1 && gh pr merge 2")

    def test_gh_pr_close_bare_with_newlines_safe(self):
        """Bare gh pr close in multi-line command — safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("echo start\ngh pr close 42\necho done")

    def test_gh_pr_close_delete_branch_with_redirect(self):
        """gh pr close --delete-branch with redirection — dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr close 42 --delete-branch > /dev/null 2>&1")

    def test_gh_pr_close_bare_with_redirect_safe(self):
        """Bare gh pr close with redirection — safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh pr close 42 > /dev/null 2>&1")

    def test_gh_pr_close_delete_branch_in_if_block(self):
        """gh pr close --delete-branch inside if block — dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("if true; then gh pr close 42 --delete-branch; fi")

    def test_gh_pr_close_delete_branch_with_url(self):
        """gh pr close --delete-branch with URL — dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh pr close https://github.com/owner/repo/pull/42 --delete-branch"
        )

    def test_gh_issue_close_with_flags_safe(self):
        """gh issue close with flags is still NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh issue close 42 --reason completed")

    def test_gh_issue_close_with_comment_safe(self):
        """gh issue close with --comment is still NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh issue close 42 --comment 'done'")

    def test_gh_issue_close_in_chain_safe(self):
        """gh issue close in chained command is NOT dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("cd /tmp && gh issue close 42")

    def test_gh_pr_close_no_word_boundary_prefix(self):
        """'agh pr close --delete-branch' is NOT detected (word boundary)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("agh pr close 42 --delete-branch")

    def test_gh_pr_closed_not_detected(self):
        """'gh pr closed' (past tense) is NOT detected."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("echo 'gh pr closed yesterday'")

    def test_gh_pr_closeable_not_detected(self):
        """'gh pr closeable' is NOT detected."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("echo 'gh pr closeable'")

    def test_herestring_cmd_sub_gh_pr_close_delete_branch(self):
        """cat <<< "$(gh pr close 42 --delete-branch)" — detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('cat <<< "$(gh pr close 42 --delete-branch)"')

    def test_herestring_cmd_sub_bare_close_safe(self):
        """cat <<< "$(gh pr close 42)" — safe (no --delete-branch)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('cat <<< "$(gh pr close 42)"')


# =============================================================================
# gh pr close: regression tests for existing patterns (TEST phase)
# =============================================================================


class TestGhPrCloseRegressionExistingPatterns:
    """Verify that adding gh pr close did not break existing dangerous patterns.

    Spot-check each existing DANGEROUS_PATTERNS entry to ensure they still work
    correctly alongside the new pattern.
    """

    def test_gh_pr_merge_still_detected(self):
        """gh pr merge is still detected after adding close pattern."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr merge 42")

    def test_force_push_still_detected(self):
        """git push --force is still detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push --force origin main")

    def test_force_push_f_flag_still_detected(self):
        """git push -f is still detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push -f origin main")

    def test_branch_D_still_detected(self):
        """git branch -D is still detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git branch -D feature-branch")

    def test_branch_delete_force_still_detected(self):
        """git branch --delete --force is still detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git branch --delete --force feature-branch")

    def test_api_merge_bypass_still_detected(self):
        """gh api merge bypass is still detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh api -X PUT repos/owner/repo/pulls/42/merge"
        )

    def test_push_to_main_still_detected(self):
        """git push origin main is still detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push origin main")

    def test_push_head_main_still_detected(self):
        """git push origin HEAD:main is still detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push origin HEAD:main")

    def test_safe_commands_still_safe(self):
        """Safe commands are still not flagged."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("git status")
        assert not is_dangerous_command("gh pr list")
        assert not is_dangerous_command("git push origin feature-branch")
        assert not is_dangerous_command("git branch -d feature-branch")

    def test_force_with_lease_still_allowed(self):
        """--force-with-lease to a non-default branch is excluded from force push detection."""
        from merge_guard_pre import is_dangerous_command

        # --force-with-lease to a feature branch is safe
        assert not is_dangerous_command("git push --force-with-lease origin feature-branch")
        # But --force-with-lease to main is STILL dangerous (matches push-to-main pattern)
        assert is_dangerous_command("git push --force-with-lease origin main")


# =============================================================================
# gh pr close: pre-hook main() entry point E2E (TEST phase)
# =============================================================================


class TestGhPrClosePreHookE2E:
    """End-to-end tests for the pre-hook main() with gh pr close commands."""

    def test_pre_hook_blocks_gh_pr_close_delete_branch(self, tmp_path, capsys):
        """Pre-hook main() blocks gh pr close --delete-branch and exits 2."""
        from merge_guard_pre import main

        input_data = {
            "tool_input": {"command": "gh pr close 42 --delete-branch"}
        }
        stdin = io.StringIO(json.dumps(input_data))

        with (
            patch("sys.stdin", stdin),
            patch("merge_guard_pre.TOKEN_DIR", tmp_path),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 2

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        # Issue #658: deny-emit must include hookEventName uniformity polish.
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

    def test_pre_hook_allows_bare_gh_pr_close(self, tmp_path, capsys):
        """Pre-hook main() allows bare gh pr close (no --delete-branch) — exits 0."""
        from merge_guard_pre import main

        input_data = {
            "tool_input": {"command": "gh pr close 42"}
        }
        stdin = io.StringIO(json.dumps(input_data))

        with (
            patch("sys.stdin", stdin),
            patch("merge_guard_pre.TOKEN_DIR", tmp_path),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"suppressOutput": True}

    def test_pre_hook_allows_gh_pr_close_delete_branch_with_token(self, tmp_path, capsys):
        """Pre-hook main() allows gh pr close --delete-branch with valid token."""
        from merge_guard_pre import main

        now = time.time()
        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"pr_number": "42", "operation_type": "close", "bound_flags": ["--delete-branch"]},
        }))

        input_data = {
            "tool_input": {"command": "gh pr close 42 --delete-branch"}
        }
        stdin = io.StringIO(json.dumps(input_data))

        with (
            patch("sys.stdin", stdin),
            patch("merge_guard_pre.TOKEN_DIR", tmp_path),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"suppressOutput": True}


# =============================================================================
# gh pr close: post-hook main() E2E (TEST phase)
# =============================================================================


class TestGhPrClosePostHookE2E:
    """End-to-end tests for the post-hook main() with gh pr close questions."""

    def test_post_hook_writes_token_for_close_pr_question(self, tmp_path, capsys):
        """Post-hook main() creates token when user approves closing a PR."""
        from merge_guard_post import main

        input_data = {
            "tool_input": {
                "questions": [{
                    "question": "Close the PR?",
                    "options": [
                        {"label": "Yes, close", "description": "Run `gh pr close 42 --delete-branch`"},
                        {"label": "Cancel", "description": "Abort"},
                    ],
                }]
            },
            "tool_response": {
                "answers": {"Close the PR?": "Yes, close"}
            },
        }
        stdin = io.StringIO(json.dumps(input_data))

        with (
            patch("sys.stdin", stdin),
            patch("merge_guard_post.TOKEN_DIR", tmp_path),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        # Token file should exist
        token_files = list(tmp_path.glob("merge-authorized-*"))
        assert len(token_files) == 1

        token_data = json.loads(token_files[0].read_text())
        assert "context" in token_data
        assert token_data["context"]["pr_number"] == "42"
        assert token_data["context"]["operation_type"] == "close"

    def test_post_hook_writes_token_for_gh_pr_close_question(self, tmp_path, capsys):
        """Post-hook main() creates token for a DANGEROUS 'gh pr close' question.
        (Under the GAP1 is_dangerous-gate a BARE `gh pr close 99` is reversible →
        mints NO token; the close must carry --delete-branch to be gated + mint.)"""
        from merge_guard_post import main

        input_data = {
            "tool_input": {
                "questions": [{
                    "question": "Close the PR?",
                    "options": [
                        {"label": "Yes, close", "description": "Run `gh pr close 99 --delete-branch`"},
                        {"label": "Cancel", "description": "Abort"},
                    ],
                }]
            },
            "tool_response": {
                "answers": {"Close the PR?": "Yes, close"}
            },
        }
        stdin = io.StringIO(json.dumps(input_data))

        with (
            patch("sys.stdin", stdin),
            patch("merge_guard_post.TOKEN_DIR", tmp_path),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        token_files = list(tmp_path.glob("merge-authorized-*"))
        assert len(token_files) == 1

    def test_post_hook_no_token_for_negative_close_answer(self, tmp_path, capsys):
        """Post-hook does NOT create token when user declines closing a PR."""
        from merge_guard_post import main

        input_data = {
            "tool_input": {
                "questions": [{"question": "Should I close PR #42?"}]
            },
            "tool_response": {
                "answers": {"Should I close PR #42?": "no"}
            },
        }
        stdin = io.StringIO(json.dumps(input_data))

        with (
            patch("sys.stdin", stdin),
            patch("merge_guard_post.TOKEN_DIR", tmp_path),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        token_files = list(tmp_path.glob("merge-authorized-*"))
        assert len(token_files) == 0

    def test_post_hook_no_token_for_close_issue_question(self, tmp_path, capsys):
        """Post-hook does NOT create token for 'close issue' questions."""
        from merge_guard_post import main

        input_data = {
            "tool_input": {
                "questions": [{"question": "Should I close issue #42?"}]
            },
            "tool_response": {
                "answers": {"Should I close issue #42?": "yes"}
            },
        }
        stdin = io.StringIO(json.dumps(input_data))

        with (
            patch("sys.stdin", stdin),
            patch("merge_guard_post.TOKEN_DIR", tmp_path),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        token_files = list(tmp_path.glob("merge-authorized-*"))
        assert len(token_files) == 0


# =============================================================================
# gh pr close: token matching edge cases (TEST phase)
# =============================================================================


class TestGhPrCloseTokenMatchingEdgeCases:
    """Additional token matching edge cases for gh pr close commands."""

    def test_token_without_op_type_denies_both(self):
        """FAIL-CLOSED (was matches-both): an untyped token (no operation_type)
        authorizes NEITHER close NOR merge, for any PR."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"pr_number": "42"}}
        assert not _token_matches_command(token, "gh pr close 42 --delete-branch")
        assert not _token_matches_command(token, "gh pr merge 42")
        assert not _token_matches_command(token, "gh pr close 99 --delete-branch")
        assert not _token_matches_command(token, "gh pr merge 99")

    def test_token_with_malformed_context_denies_close(self):
        """FAIL-CLOSED (F-READ-1, was permissive): a non-dict context proves
        nothing → it does not authorize a close."""
        from merge_guard_pre import _token_matches_command

        token = {"context": "not-a-dict"}
        assert not _token_matches_command(token, "gh pr close 42 --delete-branch")

    def test_token_with_no_context_key_denies_close(self):
        """FAIL-CLOSED (was allow-through): a token with no context key has no
        operation_type → it does not authorize a close."""
        from merge_guard_pre import _token_matches_command

        token = {}
        assert not _token_matches_command(token, "gh pr close 42 --delete-branch")

    def test_close_token_cross_operation_blocked(self):
        """Close token cannot authorize merge (operation_type enforced)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"pr_number": "42", "operation_type": "close"}}
        assert not _token_matches_command(token, "gh pr merge 42")

    def test_merge_token_cross_operation_blocked(self):
        """Merge token cannot authorize close (operation_type enforced)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"pr_number": "42", "operation_type": "merge"}}
        assert not _token_matches_command(token, "gh pr close 42 --delete-branch")


# =============================================================================
# gh pr close: full pre-to-post integration (TEST phase)
# =============================================================================


class TestGhPrCloseFullIntegration:
    """Integration tests for the complete close flow: question -> token -> command.

    Tests the full pipeline: post-hook writes token on approval,
    pre-hook reads token and authorizes the matching command.
    """

    def test_close_flow_approve_then_execute(self, tmp_path):
        """Full flow: user approves a command-bearing close question → token
        minted from the command → matching command allowed."""
        from shared.merge_guard_common import extract_command_context
        from merge_guard_post import is_merge_question, write_token
        from merge_guard_pre import check_merge_authorization

        # Step 1: the command-driven hint fires on the embedded close command.
        question = "Should I run `gh pr close 42 --delete-branch`?"
        assert is_merge_question(question)

        # Step 2: mint a token from the command (the command-anchored SSOT).
        context = extract_command_context("gh pr close 42 --delete-branch")
        assert context["pr_number"] == "42"
        assert context["operation_type"] == "close"
        token_path = write_token(context, token_dir=tmp_path)
        assert token_path is not None

        # Step 3: pre-hook authorizes the matching close --delete-branch command.
        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert result is None

    def test_close_flow_token_blocks_after_budget_exhausted(self, tmp_path):
        """After MAX_USES close ops authorized, next attempt blocks (#720 Bug C)."""
        from shared.merge_guard_common import MAX_USES, extract_command_context
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        context = extract_command_context("gh pr close 42 --delete-branch")
        write_token(context, token_dir=tmp_path)

        # MAX_USES close ops all authorized
        for _ in range(MAX_USES):
            assert check_merge_authorization(
                "gh pr close 42 --delete-branch", token_dir=tmp_path
            ) is None

        # Next attempt: blocked
        result = check_merge_authorization(
            "gh pr close 42 --delete-branch", token_dir=tmp_path
        )
        assert result is not None

    def test_close_flow_token_rejects_wrong_pr(self, tmp_path):
        """Token for PR #42 does not authorize closing PR #99."""
        from shared.merge_guard_common import extract_command_context
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        context = extract_command_context("gh pr close 42 --delete-branch")
        write_token(context, token_dir=tmp_path)

        result = check_merge_authorization("gh pr close 99 --delete-branch", token_dir=tmp_path)
        assert result is not None
        assert "does not match" in result.lower()

    def test_close_token_does_not_authorize_merge(self, tmp_path):
        """Token for close PR #42 does NOT authorize merge (operation_type enforced)."""
        from shared.merge_guard_common import extract_command_context
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        context = extract_command_context("gh pr close 42 --delete-branch")
        write_token(context, token_dir=tmp_path)

        # Close token cannot authorize merge
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "does not match" in result.lower()

    def test_merge_token_does_not_authorize_close(self, tmp_path):
        """Token from a merge command does NOT authorize close (op_type enforced)."""
        from shared.merge_guard_common import extract_command_context
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        context = extract_command_context("gh pr merge 42")
        write_token(context, token_dir=tmp_path)

        # Merge token cannot authorize close --delete-branch
        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert result is not None
        assert "does not match" in result.lower()

    def test_bare_close_allowed_without_token(self, tmp_path):
        """Bare gh pr close (no --delete-branch) is allowed without any token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("gh pr close 42", token_dir=tmp_path)
        assert result is None


# =============================================================================
# detect_command_operation_type unit tests
# =============================================================================


class TestDetectCommandOperationType:
    """Direct unit tests for detect_command_operation_type helper."""

    def test_merge_command(self):
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("gh pr merge 42") == "merge"

    def test_close_command(self):
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("gh pr close 42") == "close"

    def test_force_push_returns_force_push(self):
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("git push --force origin main") == "force-push"

    def test_branch_delete_returns_branch_delete(self):
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("git branch -D feature") == "branch-delete"

    def test_force_push_short_flag(self):
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("git push -f origin main") == "force-push"

    def test_force_push_with_lease_to_topic_branch_returns_none(self):
        """git push --force-with-lease WITHOUT a main/master target stays None.

        The push-to-main detect arm covers lease pushes to a DEFAULT branch
        (the lease fold); a lease push to a topic branch is unrecognized
        because no default-branch target matches — not because of a lease
        exclusion. Force-push classification still excludes lease forms
        entirely.
        """
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("git push --force-with-lease origin feature") is None

    def test_api_ref_delete_classified_as_branch_delete(self):
        """API DELETE on git/refs is a branch-delete class operation."""
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type(
            "gh api -X DELETE repos/owner/repo/git/refs/heads/feature"
        ) == "branch-delete"

    def test_api_ref_patch_classified_as_force_push(self):
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type(
            "gh api -X PATCH repos/owner/repo/git/refs/heads/main -f sha=abc"
        ) == "force-push"

    def test_curl_ref_patch_classified_as_force_push(self):
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type(
            "curl -X PATCH https://api.github.com/repos/o/r/git/refs/heads/main"
        ) == "force-push"

    def test_branch_delete_force_long_form(self):
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("git branch --delete --force feature") == "branch-delete"

    def test_unknown_command_returns_none(self):
        """Non-destructive shapes correctly return None."""
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("ls -la") is None
        assert detect_command_operation_type("git status") is None


# =============================================================================
# Line continuation normalization tests
# =============================================================================


class TestLineContinuationNormalization:
    """Tests that bash line continuations (\\<newline>) don't bypass pattern matching."""

    def test_close_with_delete_branch_split(self):
        """gh pr close with --delete-branch split across lines is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr close 42 \\\n--delete-branch")

    def test_merge_split_across_lines(self):
        """gh pr merge split across lines is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr merge \\\n42")

    def test_force_push_split_across_lines(self):
        """git push --force split across lines is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git push \\\n--force origin main")

    def test_branch_delete_split_across_lines(self):
        """git branch -D split across lines is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git branch \\\n-D feature")

    def test_multiple_continuations(self):
        """Multiple line continuations in a single command are all normalized."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh \\\npr \\\nmerge \\\n42")

    def test_no_false_positive_on_literal_backslash_n(self):
        """Literal backslash-n in text (not a line continuation) is not affected."""
        from merge_guard_pre import is_dangerous_command

        # This is a literal \\n inside a string, not a line continuation
        assert not is_dangerous_command("echo 'line1\\nline2'")


# =============================================================================
# gh --repo / -R / --hostname flag bypass tests (#267)
# =============================================================================


class TestGhGlobalFlagBypass:
    """Tests that gh global flags (--repo, -R, --hostname) between 'gh' and
    subcommand don't bypass dangerous pattern detection.

    Issue #267: gh CLI allows global flags before the subcommand, e.g.,
    'gh --repo owner/repo pr merge 42'. All gh-prefixed patterns must account
    for optional flags between 'gh' and the subcommand.
    """

    # --- gh pr merge with global flags ---

    def test_merge_with_repo_flag(self):
        """'gh --repo owner/repo pr merge 42' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh --repo owner/repo pr merge 42")

    def test_merge_with_short_repo_flag(self):
        """'gh -R owner/repo pr merge 42' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh -R owner/repo pr merge 42")

    def test_merge_with_hostname_flag(self):
        """'gh --hostname github.example.com pr merge 42' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh --hostname github.example.com pr merge 42")

    def test_merge_with_multiple_global_flags(self):
        """'gh --repo owner/repo --hostname host pr merge 42' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo --hostname github.example.com pr merge 42"
        )

    def test_merge_with_repo_equals_syntax(self):
        """'gh --repo=owner/repo pr merge 42' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh --repo=owner/repo pr merge 42")

    # --- gh pr close --delete-branch with global flags ---

    def test_close_delete_branch_with_repo_flag(self):
        """'gh --repo owner/repo pr close 42 --delete-branch' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo pr close 42 --delete-branch"
        )

    def test_close_delete_branch_with_short_repo_flag(self):
        """'gh -R owner/repo pr close 42 --delete-branch' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh -R owner/repo pr close 42 --delete-branch")

    def test_close_delete_branch_with_hostname_flag(self):
        """'gh --hostname host pr close 42 --delete-branch' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --hostname github.example.com pr close 42 --delete-branch"
        )

    def test_close_delete_branch_with_multiple_flags(self):
        """'gh --repo X --hostname Y pr close 42 --delete-branch' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo --hostname github.example.com pr close 42 --delete-branch"
        )

    def test_close_delete_branch_with_repo_equals(self):
        """'gh --repo=owner/repo pr close 42 --delete-branch' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo=owner/repo pr close 42 --delete-branch"
        )

    def test_close_delete_branch_reversed_with_repo_flag(self):
        """'--delete-branch before gh --repo ... pr close' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "--delete-branch gh --repo owner/repo pr close 42"
        )

    # --- bare gh pr close with global flags is SAFE ---

    def test_bare_close_with_repo_flag_is_safe(self):
        """'gh --repo owner/repo pr close 42' (no --delete-branch) is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh --repo owner/repo pr close 42")

    def test_bare_close_with_short_repo_flag_is_safe(self):
        """'gh -R owner/repo pr close 42' (no --delete-branch) is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh -R owner/repo pr close 42")

    # --- gh api with global flags ---

    def test_api_merge_with_repo_flag(self):
        """'gh --repo owner/repo api ... merge -X PUT' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo api repos/owner/repo/pulls/42/merge -X PUT"
        )

    def test_api_merge_with_short_repo_flag(self):
        """'gh -R owner/repo api ... merge -X PUT' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh -R owner/repo api repos/owner/repo/pulls/42/merge -X PUT"
        )

    def test_api_merge_with_hostname_flag(self):
        """'gh --hostname host api ... merge --method PUT' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --hostname github.example.com api repos/owner/repo/pulls/42/merge --method PUT"
        )

    def test_api_merge_with_multiple_flags(self):
        """'gh --repo X --hostname Y api ... merge -X PUT' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo --hostname github.example.com api repos/owner/repo/pulls/42/merge -X PUT"
        )

    def test_api_read_with_repo_flag_is_safe(self):
        """'gh --repo owner/repo api ... merge' (no mutating method) is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh --repo owner/repo api repos/owner/repo/pulls/42/merge"
        )

    # --- detect_command_operation_type with global flags ---

    def test_operation_type_merge_with_repo_flag(self):
        """detect_command_operation_type finds 'merge' with --repo flag."""
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("gh --repo owner/repo pr merge 42") == "merge"

    def test_operation_type_close_with_repo_flag(self):
        """detect_command_operation_type finds 'close' with --repo flag."""
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("gh --repo owner/repo pr close 42") == "close"

    def test_operation_type_merge_with_short_repo_flag(self):
        """detect_command_operation_type finds 'merge' with -R flag."""
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("gh -R owner/repo pr merge 42") == "merge"

    def test_operation_type_close_with_short_repo_flag(self):
        """detect_command_operation_type finds 'close' with -R flag."""
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("gh -R owner/repo pr close 42") == "close"

    def test_operation_type_none_with_repo_flag(self):
        """detect_command_operation_type returns None for non-PR gh commands."""
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("gh --repo owner/repo issue list") is None

    # --- _token_matches_command with global flags ---

    def test_token_pr_match_with_repo_flag(self):
        """Typed merge token PR-number matching works past a --repo global flag
        (the load-bearing anti-bypass PR extraction is preserved)."""
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "merge",
                "pr_number": 42,
                "bound_flags": ["--repo=owner/repo"],  # #1042: -R/--repo now bound
            }
        }
        assert _token_matches_command(token, "gh --repo owner/repo pr merge 42")

    def test_token_pr_mismatch_with_repo_flag(self):
        """Typed merge token PR-number mismatch detected past a --repo flag.

        The token carries the SAME bound flags the command mints (--repo=owner/repo)
        so the #1042 set-equality flag axis MATCHES — leaving the PR-number axis
        (42 vs 99) as the sole load-bearing REFUSE reason. Without the matching
        bound_flags the refusal would ride the flag axis (token []) and this test
        would stay green even if PR-mismatch detection regressed (vacuous).
        """
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "merge",
                "pr_number": 42,
                "bound_flags": ["--repo=owner/repo"],
            }
        }
        assert not _token_matches_command(token, "gh --repo owner/repo pr merge 99")

    def test_token_op_type_with_repo_flag(self):
        """Typed merge token (op + pr) matching works past a --repo global flag."""
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "merge",
                "pr_number": "42",
                "bound_flags": ["--repo=owner/repo"],  # #1042: -R/--repo now bound
            }
        }
        assert _token_matches_command(token, "gh --repo owner/repo pr merge 42")

    def test_token_op_type_mismatch_with_repo_flag(self):
        """Token operation type mismatch detected with --repo flag."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "merge"}}
        assert not _token_matches_command(
            token, "gh --repo owner/repo pr close 42 --delete-branch"
        )

    # --- Full authorization flow with global flags ---

    def test_merge_with_repo_flag_blocked_without_token(self, tmp_path):
        """'gh --repo owner/repo pr merge 42' blocked without token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization(
            "gh --repo owner/repo pr merge 42", token_dir=tmp_path
        )
        assert result is not None
        assert "approval" in result.lower()

    def test_close_delete_branch_with_repo_flag_blocked(self, tmp_path):
        """'gh --repo owner/repo pr close 42 --delete-branch' blocked without token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization(
            "gh --repo owner/repo pr close 42 --delete-branch", token_dir=tmp_path
        )
        assert result is not None
        assert "approval" in result.lower()

    # --- Line continuation combined with global flags ---

    def test_merge_with_repo_flag_line_continuation(self):
        """'gh --repo owner/repo \\ pr merge 42' is detected after normalization."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh --repo owner/repo \\\npr merge 42")

    def test_close_delete_branch_repo_flag_line_continuation(self):
        """'gh --repo owner/repo \\ pr close 42 --delete-branch' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo \\\npr close 42 --delete-branch"
        )


# =============================================================================
# gh global flag bypass — comprehensive edge cases, adversarial, integration
# =============================================================================


class TestGhGlobalFlagBypassEdgeCases:
    """Comprehensive edge case, adversarial, and integration tests for the
    gh global flag bypass fix (issue #267).

    Supplements TestGhGlobalFlagBypass (coder smoke tests) with:
    - Unusual flag values (dots, slashes, special chars, long values)
    - Greedy matching concerns (over-match into unrelated commands)
    - Stripping helper interactions (echo, var assign, heredoc, comment)
    - Adversarial bypass vectors (creative flag placement, obfuscation)
    - Token matching with global flags (PR extraction, operation type)
    """

    # --- Unusual flag values ---

    def test_repo_with_nested_org(self):
        """Repo value with nested org path (org/suborg/repo) is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo my-org/my-subrepo pr merge 42"
        )

    def test_repo_with_dots_in_name(self):
        """Repo value containing dots (e.g., example.com/repo) is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh --repo my-org/my.repo.name pr merge 42")

    def test_repo_with_hyphens_and_underscores(self):
        """Repo value with mixed separators is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo my_org-name/my_repo-name pr merge 42"
        )

    def test_hostname_with_port(self):
        """Hostname value including port number is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --hostname github.example.com:8443 pr merge 42"
        )

    def test_repo_equals_with_special_chars(self):
        """--repo=value with special chars in value is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh --repo=my-org/my.repo pr merge 42")

    def test_short_repo_with_long_path(self):
        """-R with a long repo path is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh -R very-long-organization-name/very-long-repository-name pr merge 42"
        )

    # --- Many flags chained ---

    def test_three_global_flags(self):
        """Three global flags before subcommand is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo --hostname host.com --help pr merge 42"
        )

    def test_five_flag_tokens(self):
        """Five flag+value tokens before subcommand is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo --hostname host.com -R other/repo --verbose --debug pr merge 42"
        )

    # --- Greedy matching: false positive defense ---
    # (?:\S+\s+)* can over-match. These verify the KNOWN behavior:
    # commands with 'pr merge' or 'pr close' appearing as arguments to
    # other gh subcommands will match. This is acceptable (false positive >
    # missed threat per the codebase philosophy), but we document it.

    def test_gh_issue_with_pr_merge_in_args_is_false_positive(self):
        """gh search issues 'pr merge' matches — known false positive.

        The (?:\\S+\\s+)* pattern eats 'search issues' tokens, then matches
        'pr merge'. This is a conservative false positive, not a bypass.
        """
        from merge_guard_pre import is_dangerous_command

        # Document the known false positive behavior
        result = is_dangerous_command("gh search issues pr merge")
        assert result is True  # Known false positive — acceptable

    def test_gh_pr_view_no_false_positive(self):
        """gh pr view (without 'merge' as separate word) is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh --repo owner/repo pr view 42")

    def test_gh_pr_list_no_false_positive(self):
        """gh pr list with --repo is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh --repo owner/repo pr list")

    def test_gh_pr_create_no_false_positive(self):
        """gh pr create with --repo is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh --repo owner/repo pr create --fill")

    def test_gh_pr_checkout_no_false_positive(self):
        """gh pr checkout with --repo is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh --repo owner/repo pr checkout 42")

    def test_gh_issue_list_no_false_positive(self):
        """gh issue list with --repo is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh --repo owner/repo issue list")

    def test_gh_release_create_no_false_positive(self):
        """gh release create with --repo is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("gh --repo owner/repo release create v1.0")

    def test_bare_close_with_multiple_flags_is_safe(self):
        """gh --repo X --hostname Y pr close (no --delete-branch) is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh --repo owner/repo --hostname host.com pr close 42"
        )

    # --- Stripping helper interactions ---

    def test_echo_flagged_merge_stripped(self):
        """echo of 'gh --repo ... pr merge' is stripped and not detected."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'echo "gh --repo owner/repo pr merge 42"'
        )

    def test_echo_flagged_close_delete_branch_stripped(self):
        """echo of 'gh --repo ... pr close --delete-branch' is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'echo "gh --repo owner/repo pr close 42 --delete-branch"'
        )

    def test_var_assign_flagged_merge_stripped(self):
        """Variable assignment of flagged merge command is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'CMD="gh --repo owner/repo pr merge 42"'
        )

    def test_comment_flagged_merge_stripped(self):
        """Comment containing flagged merge command is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "# gh --repo owner/repo pr merge 42"
        )

    def test_heredoc_flagged_merge_stripped(self):
        """Heredoc containing flagged merge command is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "cat << 'EOF'\ngh --repo owner/repo pr merge 42\nEOF"
        )

    def test_echo_flagged_api_merge_stripped(self):
        """echo of 'gh --repo ... api ... merge -X PUT' is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'echo "gh --repo owner/repo api repos/o/r/pulls/1/merge -X PUT"'
        )

    def test_printf_flagged_merge_stripped(self):
        """printf of flagged merge command is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "printf 'gh --repo owner/repo pr merge 42'"
        )

    def test_git_commit_msg_flagged_merge_stripped(self):
        """git commit -m with flagged merge text is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'git commit -m "feat: gh --repo owner/repo pr merge 42"'
        )

    def test_herestring_flagged_merge_stripped(self):
        """Here-string containing flagged merge command is stripped."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'grep -c merge <<< "gh --repo owner/repo pr merge 42"'
        )

    # --- Stripping helpers that PRESERVE dangerous content ---

    def test_echo_piped_to_bash_with_flags_detected(self):
        """echo of flagged merge piped to bash IS dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'echo "gh --repo owner/repo pr merge 42" | bash'
        )

    def test_eval_var_with_flags_detected(self):
        """Variable with flagged merge passed to eval IS dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'CMD="gh --repo owner/repo pr merge 42" && eval $CMD'
        )

    def test_var_expanded_with_flags_detected(self):
        """Variable with flagged merge expanded bare IS dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'CMD="gh --repo owner/repo pr merge 42" && $CMD'
        )

    def test_heredoc_to_bash_with_flags_detected(self):
        """Heredoc with flagged merge fed to bash IS dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "bash << 'EOF'\ngh --repo owner/repo pr merge 42\nEOF"
        )

    def test_command_substitution_in_echo_with_flags_detected(self):
        """echo with $() containing flagged merge IS dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'echo "$(gh --repo owner/repo pr merge 42)"'
        )

    def test_process_substitution_to_bash_with_flags_detected(self):
        """bash <(echo 'flagged merge') IS dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "bash <(echo 'gh --repo owner/repo pr merge 42')"
        )

    # --- Chained commands with flags ---

    def test_chained_and_flagged_merge(self):
        """Safe command && flagged merge is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "echo done && gh --repo owner/repo pr merge 42"
        )

    def test_chained_semicolon_flagged_merge(self):
        """Safe command ; flagged merge is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "echo done; gh --repo owner/repo pr merge 42"
        )

    def test_subshell_flagged_merge(self):
        """$(flagged merge) in subshell is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "$(gh --repo owner/repo pr merge 42)"
        )

    def test_env_var_prefix_flagged_merge(self):
        """ENV=val flagged merge is dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "GH_TOKEN=abc gh --repo owner/repo pr merge 42"
        )

    # --- Line continuation combined with global flags (extended) ---

    def test_flag_value_split_by_line_continuation(self):
        """Flag value split across line continuation is detected."""
        from merge_guard_pre import is_dangerous_command

        # gh --repo \<newline>owner/repo pr merge 42
        assert is_dangerous_command("gh --repo \\\nowner/repo pr merge 42")

    def test_multiple_flags_with_line_continuations(self):
        """Multiple flags each split by line continuation is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo \\\nowner/repo \\\n--hostname \\\nhost.com \\\npr merge 42"
        )

    def test_api_merge_with_flags_and_line_continuation(self):
        """API merge with flags and line continuation is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --repo owner/repo \\\napi repos/o/r/pulls/1/merge \\\n-X PUT"
        )

    def test_close_delete_branch_flags_all_line_continued(self):
        """Close --delete-branch with flags and max line continuations."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh \\\n--repo \\\nowner/repo \\\npr \\\nclose \\\n42 \\\n--delete-branch"
        )

    # --- Adversarial: creative bypass attempts ---

    def test_repo_value_contains_pr_merge(self):
        """--repo value that itself contains 'pr merge' is still dangerous.

        gh --repo pr merge — this looks like --repo flag with value 'pr'
        followed by actual 'merge', but the pattern catches it because
        (?:\\S+\\s+)* eats '--repo' and 'pr' is left as subcommand.
        Actually 'gh --repo pr merge' would parse as gh --repo=pr merge=subcommand.
        """
        from merge_guard_pre import is_dangerous_command

        # This is ambiguous but should be caught (conservative)
        assert is_dangerous_command("gh --repo pr merge 42")

    def test_flag_that_looks_like_subcommand(self):
        """Flag value 'pr' followed by real subcommand tokens."""
        from merge_guard_pre import is_dangerous_command

        # gh --hostname pr merge 42 — hostname=pr, subcommand=merge
        # Pattern sees: gh + '--hostname pr ' (eaten by flags) + 'merge' — but
        # we need 'pr\s+merge' to match, so this should still match
        assert is_dangerous_command("gh --hostname pr merge 42")

    def test_double_pr_merge_in_command(self):
        """Command with 'pr merge' appearing twice — still dangerous."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "echo 'test pr merge' && gh --repo owner/repo pr merge 42"
        )

    def test_tabs_instead_of_spaces(self):
        """Tabs between gh and flags and subcommand — still matches \\s+."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh\t--repo\towner/repo\tpr\tmerge\t42")

    def test_multiple_spaces_between_tokens(self):
        """Multiple spaces between tokens — still matches \\s+."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh   --repo   owner/repo   pr   merge   42"
        )

    def test_mixed_whitespace(self):
        """Mixed tabs and spaces — still matches \\s+."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh \t --repo \t owner/repo \t pr \t merge 42"
        )

    # --- Token matching: PR number extraction with global flags ---

    def test_token_pr_extraction_with_two_flags(self):
        """PR number extracted correctly past two global flags (typed token)."""
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "merge",
                "pr_number": 42,
                "bound_flags": ["--repo=owner/repo"],  # #1042: --hostname unbound
            }
        }
        assert _token_matches_command(
            token, "gh --repo owner/repo --hostname host.com pr merge 42"
        )

    def test_token_pr_extraction_with_equals_syntax(self):
        """PR number extracted correctly with --repo=value syntax (typed token)."""
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "merge",
                "pr_number": 42,
                "bound_flags": ["--repo=owner/repo"],  # #1042: --repo=value form
            }
        }
        assert _token_matches_command(
            token, "gh --repo=owner/repo pr merge 42"
        )

    def test_token_pr_mismatch_with_multiple_flags(self):
        """PR number mismatch detected past multiple global flags (typed token).

        --repo is bound (#1042); --hostname is not. The token carries the bound
        --repo so the flag axis matches and the PR-number axis (42 vs 99) is the
        sole load-bearing mismatch — keeping the test non-vacuous on PR detection.
        """
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "merge",
                "pr_number": 42,
                "bound_flags": ["--repo=owner/repo"],
            }
        }
        assert not _token_matches_command(
            token, "gh --repo owner/repo --hostname host.com pr merge 99"
        )

    def test_token_pr_extraction_close_with_flags(self):
        """PR number extracted from a close command past global flags (typed)."""
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "close",
                "pr_number": 100,
                # #1042: -R bound; GAP2 — --delete-branch is now BOUND on close too, so
                # a faithful close --delete-branch token carries BOTH.
                "bound_flags": ["--delete-branch", "--repo=owner/repo"],
            }
        }
        assert _token_matches_command(
            token, "gh -R owner/repo pr close 100 --delete-branch"
        )

    def test_token_pr_mismatch_close_with_flags(self):
        """PR number mismatch in a close command past global flags (typed).

        On the close op-class -R/--repo is bound; --delete-branch is the close
        op-trigger (bound via op_type, NOT in the denylist). The token carries the
        bound --repo so the flag axis matches and the PR-number axis (100 vs 200)
        is the sole load-bearing mismatch — keeping the test non-vacuous.
        """
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "close",
                "pr_number": 100,
                "bound_flags": ["--repo=owner/repo"],
            }
        }
        assert not _token_matches_command(
            token, "gh -R owner/repo pr close 200 --delete-branch"
        )

    def test_token_op_type_close_with_multiple_flags(self):
        """Operation type 'close' detected with multiple global flags."""
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type(
            "gh --repo owner/repo --hostname host.com pr close 42"
        ) == "close"

    def test_token_op_type_merge_with_equals_syntax(self):
        """Operation type 'merge' detected with --repo=value syntax."""
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type(
            "gh --repo=owner/repo pr merge 42"
        ) == "merge"

    # --- Full authorization flow with flags and valid tokens ---

    def test_flagged_merge_authorized_with_matching_token(self, tmp_path):
        """Flagged merge command authorized by matching token."""
        from merge_guard_pre import check_merge_authorization, TOKEN_PREFIX
        import time, json, os

        token_data = {
            "expires_at": time.time() + 300,
            "context": {
                "operation_type": "merge",
                "pr_number": 42,
                "bound_flags": ["--repo=owner/repo"],  # #1042: -R/--repo now bound
            },
        }
        token_path = tmp_path / f"{TOKEN_PREFIX}test"
        token_path.write_text(json.dumps(token_data))
        os.chmod(token_path, 0o600)

        result = check_merge_authorization(
            "gh --repo owner/repo pr merge 42", token_dir=tmp_path
        )
        assert result is None  # Authorized

    def test_flagged_close_blocked_by_merge_token(self, tmp_path):
        """Flagged close command blocked when only merge token exists."""
        from merge_guard_pre import check_merge_authorization, TOKEN_PREFIX
        import time, json, os

        token_data = {
            "expires_at": time.time() + 300,
            "context": {"operation_type": "merge"},
        }
        token_path = tmp_path / f"{TOKEN_PREFIX}test"
        token_path.write_text(json.dumps(token_data))
        os.chmod(token_path, 0o600)

        result = check_merge_authorization(
            "gh --repo owner/repo pr close 42 --delete-branch",
            token_dir=tmp_path,
        )
        assert result is not None  # Blocked — wrong operation type

    def test_flagged_merge_blocked_by_wrong_pr_token(self, tmp_path):
        """Flagged merge for PR 42 blocked when token is for PR 99."""
        from merge_guard_pre import check_merge_authorization, TOKEN_PREFIX
        import time, json, os

        token_data = {
            "expires_at": time.time() + 300,
            "context": {"operation_type": "merge", "pr_number": 99},
        }
        token_path = tmp_path / f"{TOKEN_PREFIX}test"
        token_path.write_text(json.dumps(token_data))
        os.chmod(token_path, 0o600)

        result = check_merge_authorization(
            "gh --repo owner/repo pr merge 42", token_dir=tmp_path
        )
        assert result is not None  # Blocked — wrong PR

    def test_api_merge_with_flags_blocked_without_token(self, tmp_path):
        """API merge with global flags blocked without token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization(
            "gh --repo owner/repo api repos/o/r/pulls/1/merge -X PUT",
            token_dir=tmp_path,
        )
        assert result is not None
        assert "approval" in result.lower()


# =============================================================================
# Review remediation: reversed --delete-branch pattern with flag variants (#269)
# =============================================================================


class TestReversedDeleteBranchWithFlags:
    """Tests for reversed --delete-branch pattern (--delete-branch before
    'gh ... pr close') with -R and --hostname flag variants.

    Supplements the single --repo test in TestGhGlobalFlagBypass.
    """

    def test_reversed_with_short_repo_flag(self):
        """'--delete-branch ... gh -R owner/repo pr close 42' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "--delete-branch gh -R owner/repo pr close 42"
        )

    def test_reversed_with_hostname_flag(self):
        """'--delete-branch ... gh --hostname host pr close 42' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "--delete-branch gh --hostname github.example.com pr close 42"
        )

    def test_reversed_with_multiple_flags(self):
        """'--delete-branch ... gh -R X --hostname Y pr close 42' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "--delete-branch gh -R owner/repo --hostname host.com pr close 42"
        )


# =============================================================================
# Review remediation: --hostname=host.com equals syntax (#269)
# =============================================================================


class TestHostnameEqualsSyntax:
    """Test for --hostname=value equals syntax (extends --repo= coverage)."""

    def test_hostname_equals_merge(self):
        """'gh --hostname=github.example.com pr merge 42' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh --hostname=github.example.com pr merge 42"
        )


# =============================================================================
# Pre-existing bypass: subcommand flags before PR number (#269 item 6)
# =============================================================================


class TestSubcommandFlagsBeforePrNumber:
    """Tests that subcommand flags between merge/close and the PR number
    don't break PR number extraction in _token_matches_command.

    e.g., 'gh pr merge --admin 42' should still extract PR number 42.
    """

    def test_merge_admin_flag_before_pr_number(self):
        """'gh pr merge --admin 42' — PR number extracted past the subcommand flag."""
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "merge",
                "pr_number": 42,
                "bound_flags": ["--admin"],  # #1042: --admin now bound
            }
        }
        assert _token_matches_command(token, "gh pr merge --admin 42")

    def test_merge_squash_flag_before_pr_number(self):
        """'gh pr merge --squash 42' — PR number extracted past the flag."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "merge", "pr_number": 42}}
        assert _token_matches_command(token, "gh pr merge --squash 42")

    def test_merge_multiple_flags_before_pr_number(self):
        """'gh pr merge --squash --delete-branch 42' — PR number extracted."""
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "merge",
                "pr_number": 42,
                # #1042: --delete-branch on MERGE is a bound side-effect; --squash
                # is a merge-method flag (unbound).
                "bound_flags": ["--delete-branch"],
            }
        }
        assert _token_matches_command(
            token, "gh pr merge --squash --delete-branch 42"
        )

    def test_close_comment_flag_before_pr_number(self):
        """'gh pr close --comment "done" 42' — PR number extracted (close op)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "close", "pr_number": 42}}
        assert _token_matches_command(token, "gh pr close --comment done 42")

    def test_merge_admin_flag_pr_number_mismatch(self):
        """'gh pr merge --admin 99' — mismatch with token for PR 42.

        The token carries the bound --admin so the #1042 flag axis matches and the
        PR-number axis (42 vs 99) is the sole load-bearing mismatch — non-vacuous
        on PR detection (without the matching flag the refusal would ride the flag
        axis and mask a PR-detection regression).
        """
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "merge",
                "pr_number": 42,
                "bound_flags": ["--admin"],
            }
        }
        assert not _token_matches_command(token, "gh pr merge --admin 99")

    def test_merge_admin_flag_with_global_flags(self):
        """'gh --repo X pr merge --admin 42' — global + subcommand flags."""
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "merge",
                "pr_number": 42,
                "bound_flags": ["--admin", "--repo=owner/repo"],  # #1042: both bound
            }
        }
        assert _token_matches_command(
            token, "gh --repo owner/repo pr merge --admin 42"
        )

    def test_merge_admin_flag_with_global_flags_mismatch(self):
        """'gh --repo X pr merge --admin 99' — mismatch with combined flags.

        The token carries BOTH bound flags the command mints (--admin and
        --repo=owner/repo) so the #1042 flag axis matches and the PR-number axis
        (42 vs 99) is the sole load-bearing mismatch — keeping the test non-vacuous.
        """
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "merge",
                "pr_number": 42,
                "bound_flags": ["--admin", "--repo=owner/repo"],
            }
        }
        assert not _token_matches_command(
            token, "gh --repo owner/repo pr merge --admin 99"
        )


# =============================================================================
# Pre-existing bypass: git -C /path flag bypass (#269 item 7)
# =============================================================================


class TestGitGlobalFlagBypass:
    """Tests that git global flags (e.g., -C /path, -c key=val) between 'git'
    and the subcommand don't bypass dangerous pattern detection.

    git allows global options before the subcommand:
    - git -C /path push --force origin main
    - git -c user.name=x push --force origin main
    - git --git-dir=/path/.git branch -D feature
    """

    # --- Force push with git global flags ---

    def test_force_push_with_C_flag(self):
        """'git -C /path push --force origin main' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git -C /tmp/repo push --force origin main")

    def test_force_push_with_git_dir_flag(self):
        """'git --git-dir=/path/.git push --force origin main' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "git --git-dir=/tmp/repo/.git push --force origin main"
        )

    def test_force_push_with_work_tree_flag(self):
        """'git --work-tree=/path push -f origin main' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "git --work-tree=/tmp/repo push -f origin main"
        )

    def test_force_push_with_multiple_git_flags(self):
        """'git -C /path -c key=val push --force origin main' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "git -C /tmp/repo -c user.name=test push --force origin main"
        )

    # --- Branch delete with git global flags ---

    def test_branch_delete_with_C_flag(self):
        """'git -C /path branch -D feature' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git -C /tmp/repo branch -D feature")

    def test_branch_delete_force_with_git_dir(self):
        """'git --git-dir=/path/.git branch --delete --force feature' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "git --git-dir=/tmp/repo/.git branch --delete --force feature"
        )

    # --- Push to main/master with git global flags ---

    def test_push_main_with_C_flag(self):
        """'git -C /path push origin main' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git -C /tmp/repo push origin main")

    def test_push_master_with_C_flag(self):
        """'git -C /path push origin master' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git -C /tmp/repo push origin master")

    def test_push_head_main_with_C_flag(self):
        """'git -C /path push origin HEAD:main' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("git -C /tmp/repo push origin HEAD:main")

    def test_push_head_master_with_git_dir(self):
        """'git --git-dir=/path push origin HEAD:master' is detected."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "git --git-dir=/tmp/repo/.git push origin HEAD:master"
        )

    # --- Safe commands with git global flags ---

    def test_git_C_status_is_safe(self):
        """'git -C /path status' is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("git -C /tmp/repo status")

    def test_git_C_log_is_safe(self):
        """'git -C /path log --oneline' is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("git -C /tmp/repo log --oneline")

    def test_git_C_push_feature_branch_is_safe(self):
        """'git -C /path push origin feature-branch' is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "git -C /tmp/repo push origin feature-branch"
        )

    def test_git_C_branch_lowercase_d_is_safe(self):
        """'git -C /path branch -d feature' (lowercase d) is safe."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command("git -C /tmp/repo branch -d feature")

    # --- Token matching: branch delete with git global flags ---

    def test_token_branch_match_with_C_flag(self):
        """Typed branch-delete token matching works past a git -C flag."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "branch-delete", "branch": "feature"}}
        assert _token_matches_command(
            token, "git -C /tmp/repo branch -D feature"
        )

    def test_token_branch_mismatch_with_C_flag(self):
        """Typed branch-delete token mismatch detected past a git -C flag."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "branch-delete", "branch": "feature"}}
        assert not _token_matches_command(
            token, "git -C /tmp/repo branch -D other-branch"
        )

    def test_token_branch_delete_force_with_git_dir(self):
        """Typed branch-delete token matches --delete --force past --git-dir."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "branch-delete", "branch": "feature"}}
        assert _token_matches_command(
            token, "git --git-dir=/tmp/.git branch --delete --force feature"
        )


# =============================================================================
# Module-load fail-closed wrapper (Issue #658 / PR #660 Future #5)
# =============================================================================


class TestModuleLoadFailClosed:
    """Tests for the module-load fail-closed wrapper in merge_guard_pre.

    Verifies that if module-level imports or pattern compilations fail at
    import time, the harness sees a structured deny output (with the
    required `hookEventName`) on stdout BEFORE the process exits — instead
    of an empty stdout that would fail open.
    """

    def _reload_with_broken_import(self, broken_module_name, monkeypatch, capsys):
        """Helper: reload merge_guard_pre with a forced ImportError on
        ``broken_module_name``. Returns (exit_code, stdout, stderr).

        Pops only ``merge_guard_pre`` from sys.modules (so its body re-runs)
        and restores it on teardown. Does NOT pop ``shared.*`` modules: the
        patched ``builtins.__import__`` raises on the broken name regardless
        of sys.modules cache state, and popping ``shared.*`` would orphan
        function references already imported by other test modules
        (e.g. ``shared.task_utils`` keeps a stale ``get_session_id`` ref to
        a popped-and-replaced ``shared.pact_context`` — observed as
        cross-file test pollution).
        """
        import importlib

        _missing = object()
        original_mgp = sys.modules.get("merge_guard_pre", _missing)
        sys.modules.pop("merge_guard_pre", None)

        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == broken_module_name or name.startswith(broken_module_name + "."):
                raise ImportError(f"simulated load failure for {broken_module_name}")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr("builtins.__import__", fake_import)

        try:
            with pytest.raises(SystemExit) as exc_info:
                importlib.import_module("merge_guard_pre")
            captured = capsys.readouterr()
            return exc_info.value.code, captured.out, captured.err
        finally:
            sys.modules.pop("merge_guard_pre", None)
            if original_mgp is not _missing:
                sys.modules["merge_guard_pre"] = original_mgp

    def test_module_load_failure_emits_deny_with_hookEventName(self, monkeypatch, capsys):
        """If shared.pact_context fails to import, the wrapper emits a
        deny output with `hookEventName: PreToolUse` and exits 2.

        Issue #658 audit anchor: hookEventName must be present in any deny
        output, including the module-load fail-closed path. Without this,
        the harness silently fails open on a broken module.
        """
        exit_code, stdout, stderr = self._reload_with_broken_import(
            "shared.pact_context", monkeypatch, capsys,
        )

        assert exit_code == 2
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "Merge guard failed to load" in output["hookSpecificOutput"]["permissionDecisionReason"]
        # Stderr must explain the cause for operator debugging.
        assert "merge_guard_pre" in stderr
        assert "simulated load failure" in stderr

    def test_module_load_failure_in_merge_guard_common_emits_deny(self, monkeypatch, capsys):
        """If shared.merge_guard_common fails to import, the wrapper still
        emits a fail-closed deny with hookEventName.
        """
        exit_code, stdout, stderr = self._reload_with_broken_import(
            "shared.merge_guard_common", monkeypatch, capsys,
        )

        assert exit_code == 2
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_pattern_compile_failure_emits_deny(self, monkeypatch, capsys):
        """If a regex compilation fails at module load, the merge-guard fails CLOSED
        with a deny (hookEventName + permissionDecision deny). DANGEROUS_PATTERNS + the
        strip closure now compile in shared.merge_guard_common (GAP1 relocation), so a
        compile failure there propagates through merge_guard_pre's `from shared...
        import` into its fail-closed except → deny.

        Simulates a malformed regex by patching ``re.compile`` to raise ``re.error``
        while shared.merge_guard_common rebuilds its pattern bank.
        """
        import importlib
        import re as _re

        _missing = object()
        original_mgp = sys.modules.get("merge_guard_pre", _missing)
        original_smc = sys.modules.get("shared.merge_guard_common", _missing)
        # Pop BOTH so shared.merge_guard_common rebuilds its pattern bank (where
        # DANGEROUS_PATTERNS now compiles) during the merge_guard_pre re-import.
        sys.modules.pop("merge_guard_pre", None)
        sys.modules.pop("shared.merge_guard_common", None)

        def _restore_modules():
            sys.modules.pop("merge_guard_pre", None)
            sys.modules.pop("shared.merge_guard_common", None)
            if original_smc is not _missing:
                sys.modules["shared.merge_guard_common"] = original_smc
            if original_mgp is not _missing:
                sys.modules["merge_guard_pre"] = original_mgp

        real_compile = _re.compile
        compile_calls = {"n": 0}

        def fake_compile(pattern, flags=0):
            # Fail compiles invoked while shared.merge_guard_common rebuilds its
            # pattern bank (it is in sys.modules from import-start, so this trips
            # inside its body — after shared.pact_context has loaded).
            if "shared.merge_guard_common" in sys.modules and "shared.pact_context" in sys.modules:
                compile_calls["n"] += 1
                if compile_calls["n"] >= 1:
                    raise _re.error("simulated bad pattern")
            return real_compile(pattern, flags)

        monkeypatch.setattr("re.compile", fake_compile)

        try:
            with pytest.raises(SystemExit) as exc_info:
                importlib.import_module("merge_guard_pre")

            captured = capsys.readouterr()
            assert exc_info.value.code == 2
            output = json.loads(captured.out)
            assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
            assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
            assert "merge_guard_pre" in captured.err
        finally:
            _restore_modules()
            # Ensure a clean shared module for subsequent tests (the failed rebuild
            # above left it absent from sys.modules).
            importlib.import_module("shared.merge_guard_common")

    def test_load_failure_audit_anchor_comment_present(self):
        """The fail-closed emit site carries the Issue #658 audit anchor.

        Audit-anchor discipline: any deny-emit site must reference the
        load-bearing schema field (`hookEventName`) in a comment so future
        edits don't silently regress it.
        """
        hook_path = Path(__file__).parent.parent / "hooks" / "merge_guard_pre.py"
        source = hook_path.read_text()

        # The shared emit helper must reference Issue #658 + hookEventName.
        assert "_emit_load_failure_deny" in source
        # Audit-anchor must be present in the helper's docstring/comments.
        def_idx = source.index("def _emit_load_failure_deny")
        # Inspect the helper body (next ~1200 chars covers docstring + body).
        helper_body = source[def_idx:def_idx + 1200]
        assert "Issue #658" in helper_body or "PR #660" in helper_body
        assert "hookEventName" in helper_body


class TestCompoundDestructiveCommandRejection:
    """Pin the compound-command rejection that closes the chained-bypass class.

    A single AskUserQuestion approval can authorize ONE destructive op.
    Chained shapes (``&&``, ``||``, ``;``, ``|``, newline) hide secondary
    destructive ops past the operator's headline-command review of the
    AskUserQuestion text. Categorically reject compound destructive
    shapes; force the operator to run one destructive op per checkpoint.
    """

    def test_compound_with_destructive_denied(self, tmp_path):
        """gh pr merge 100 && gh pr merge 999 → denied even with token for 100."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        # Token for the headline op exists
        write_token(
            {"pr_number": "100", "operation_type": "merge"},
            token_dir=tmp_path,
        )
        cmd = "gh pr merge 100 && gh pr merge 999 --admin"
        result = check_merge_authorization(cmd, token_dir=tmp_path)
        assert result is not None, "compound destructive must be denied"
        assert "Compound destructive" in result
        assert "&&" in result or "||" in result or ";" in result

    def test_pipe_with_destructive_denied(self, tmp_path):
        """Honest-mistake ≥2-narrowing: `gh pr merge 100 | tee` is ONE destructive
        leg + a benign pipe → NO LONGER compound. But the single merge op is STILL
        is_dangerous-gated, so with no token it is DENIED — via the normal single-op
        approval gate, NOT the compound gate (no under-gate; the op is still caught)."""
        from merge_guard_pre import check_merge_authorization, is_compound_destructive_command, is_dangerous_command

        cmd = "gh pr merge 100 | tee /tmp/out"
        assert not is_compound_destructive_command(cmd)   # single destructive leg
        assert is_dangerous_command(cmd)                  # the merge op is still gated
        result = check_merge_authorization(cmd, token_dir=tmp_path)
        assert result is not None                         # denied (single op, no token)
        assert "Compound destructive" not in result       # NOT via the compound gate

    def test_semicolon_with_destructive_denied(self, tmp_path):
        """`echo go ; git push --force origin main` — ONE destructive leg (force-push)
        + a benign leg → not compound, but the force-push is is_dangerous-gated →
        still DENIED via the single-op gate (not compound)."""
        from merge_guard_pre import check_merge_authorization, is_compound_destructive_command, is_dangerous_command

        cmd = "echo go ; git push --force origin main"
        assert not is_compound_destructive_command(cmd)
        assert is_dangerous_command(cmd)
        result = check_merge_authorization(cmd, token_dir=tmp_path)
        assert result is not None
        assert "Compound destructive" not in result

    def test_newline_with_destructive_denied(self, tmp_path):
        """`ls\\ngh pr merge 99` — ONE destructive leg (merge) + a benign leg → not
        compound, but the merge is is_dangerous-gated → still DENIED via the single-op
        gate (not compound)."""
        from merge_guard_pre import check_merge_authorization, is_compound_destructive_command, is_dangerous_command

        cmd = "ls\ngh pr merge 99"
        assert not is_compound_destructive_command(cmd)
        assert is_dangerous_command(cmd)
        result = check_merge_authorization(cmd, token_dir=tmp_path)
        assert result is not None
        assert "Compound destructive" not in result

    def test_compound_safe_commands_allowed(self, tmp_path):
        """Safe compounds without DANGEROUS_PATTERNS are NOT rejected.

        ``ls && pwd`` contains a compound shape but no destructive sub-op,
        so check_merge_authorization returns None (allow).
        """
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("ls && pwd", token_dir=tmp_path)
        assert result is None, "safe compound must be allowed"

    def test_single_destructive_with_token_still_works(self, tmp_path):
        """Regression-protection: single destructive op + valid token = allow."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token(
            {"pr_number": "100", "operation_type": "merge"},
            token_dir=tmp_path,
        )
        result = check_merge_authorization("gh pr merge 100", token_dir=tmp_path)
        assert result is None, "single destructive with token must be allowed"

    # Regression pins for the five true-positive compound arms after the
    # _COMPOUND_OPS_RE tightening. These exercise is_compound_destructive_command
    # directly so the predicate is anchored independently of token state.
    def test_compound_double_amp_still_matches(self):
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "gh pr merge 100 && gh pr merge 999 --admin"
        ) is True

    def test_compound_double_pipe_still_matches(self):
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "gh pr merge 100 || gh pr merge 999 --admin"
        ) is True

    def test_compound_semicolon_still_matches(self):
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "gh pr merge 100; gh pr merge 999 --admin"
        ) is True

    def test_bare_pipe_single_destructive_not_compound(self):
        """Honest-mistake ≥2: `gh pr merge 100 | tee logfile` is ONE destructive leg
        + a benign pipe → NOT compound. The single merge op stays is_dangerous-gated."""
        from merge_guard_pre import is_compound_destructive_command, is_dangerous_command

        assert is_compound_destructive_command("gh pr merge 100 | tee logfile") is False
        assert is_dangerous_command("gh pr merge 100 | tee logfile") is True

    def test_compound_newline_still_matches(self):
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "gh pr merge 100\ngh pr merge 999"
        ) is True

    # New negatives — these previously triggered the compound predicate
    # via the loose `[&;|\n]` character class. The tightened pattern
    # excludes file-descriptor redirects and clobber redirects.
    def test_fd_merge_stderr_to_stdout_not_compound(self, tmp_path):
        """`gh pr merge 100 2>&1` no longer flagged as compound (was a false positive)."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization, is_compound_destructive_command

        cmd = "gh pr merge 100 2>&1"
        assert is_compound_destructive_command(cmd) is False
        # End-to-end: with valid token, the FD redirect should NOT be denied
        # as compound. (Token-match still applies; check authorization passes.)
        write_token(
            {"pr_number": "100", "operation_type": "merge"},
            token_dir=tmp_path,
        )
        result = check_merge_authorization(cmd, token_dir=tmp_path)
        assert result is None, "FD redirect must not be flagged as compound"

    def test_fd_merge_stdout_to_stderr_not_compound(self):
        """`git push --force 1>&2` — other-direction FD merge."""
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "git push --force origin main 1>&2"
        ) is False

    def test_fd_merge_with_stdout_redirect_not_compound(self):
        """`gh pr merge 100 >foo 2>&1` — combined stdout-file + stderr-to-stdout."""
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "gh pr merge 100 >foo 2>&1"
        ) is False

    def test_fd_duplication_input_not_compound(self):
        """`gh pr merge 100 3<&0` — FD-duplication on stdin."""
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "gh pr merge 100 3<&0"
        ) is False

    def test_clobber_redirect_not_compound(self):
        """`gh pr merge 100 >| file` — `>|` clobber redirect (the `|` is preceded by `>`)."""
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "gh pr merge 100 >| file"
        ) is False


class TestSplitIntoLegsExtractionParity:
    """Regression gate for the _split_into_legs extraction.

    is_compound_destructive_command's leg split was extracted verbatim into the
    shared _split_into_legs helper (so the compound-refuse and the read-side
    single-destructive-leg isolation can never see divergent leg boundaries). This
    pins that the extraction is BEHAVIOR-IDENTICAL to the prior inline split — the
    one transcription error that would matter is slicing the legs from the masked
    `view` instead of `stripped`, which this differential catches. The
    is_compound_destructive_command verdict suite is the companion gate (the
    True/False outcomes are the contract); this makes 'identical, not regress'
    executable at the leg-boundary level.
    """

    @staticmethod
    def _old_inline_split(command):
        """The pre-extraction inline split, reproduced independently (sans the
        no-operator early-return, which only short-circuited the verdict, not the
        legs). Slices from `stripped`, offsets located on the masked + FD-
        neutralized view — exactly as the inline body did."""
        from shared.merge_guard_common import (
            _COMPOUND_OPS_RE,
            _FD_REDIRECT_RE,
            _mask_shell_quotes,
            _normalize_line_continuations,
            _strip_non_executable_content,
        )

        stripped = _strip_non_executable_content(
            _normalize_line_continuations(command)
        )
        view = _FD_REDIRECT_RE.sub(
            lambda mm: " " * len(mm.group()), _mask_shell_quotes(stripped)
        )
        legs, last = [], 0
        for mm in _COMPOUND_OPS_RE.finditer(view):
            legs.append(stripped[last:mm.start()])
            last = mm.end()
        legs.append(stripped[last:])
        return legs

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr merge 5",                                   # one leg, no operator
            "gh pr close 5 -d && git branch -Df victim",       # &&
            "gh pr merge 100 && gh pr close 999 --delete-branch",
            "gh pr merge 5 && rm -rf /",                        # gh + rm
            "gh pr merge 5 ; echo done",                        # ;
            "gh pr merge 5 | tail",                             # |
            "gh pr merge 5 > out.log",                          # redirect (one leg)
            "gh pr merge 5 &",                                  # background
            "git push --force origin main 2>&1 | rm -rf ~",     # multi-char redirect then |
            'gh pr merge 5 --subject "a; b" && gh pr close 6',  # quoted ; is inert
            "gh pr close 1058\ngh pr merge 999 --squash",       # newline operator
            "gh pr merge 5 --admin ; gh pr view 5 --repo o/x",  # benign neighbor leg
            'gh pr merge 5 --body "x | y > z"',                 # quoted metachars, one leg
            "gh pr merge 5 &> out.log",                         # and-redirect (one leg)
        ],
    )
    def test_split_matches_prior_inline_split(self, command):
        from shared.merge_guard_common import _split_into_legs

        assert _split_into_legs(command) == self._old_inline_split(command)


class TestBenignContinuationGuarantee:
    """Pin the 'single destructive op + benign continuation' idiom.

    A faithful agent appends a benign viewer/filter/redirect/background to a
    single approved destructive op to confirm the result without a separate API
    query (e.g. ``gh pr merge 5 --squash | tail``). Under the honest-mistake
    model that is ONE destructive leg — NOT a >=2-destructive compound — so it
    must mint a token and must not be refused as compound. This class pins the
    whole continuation family so a future hardening change cannot silently
    re-break the idiom.

    The guarantee is UNIVERSAL across the recognized destructive ops — gh pr
    merge, gh pr close, git force-push, AND git branch-delete: a single such op
    plus any benign continuation mints a token AND the read side AUTHORIZES the
    continued command. Each op's target parser re-derives its target from the
    single destructive leg regardless of trailing continuation / redirect tokens
    (the positional extractors truncate at the first benign terminator on the
    quote-masked view), so the minted single-op token still matches.

    Non-vacuity is anchored:
      * the ``| bash`` pipe-to-shell boundary — a benign continuation is bounded:
        ``_has_pipe_to_shell`` recognizes ``| bash`` / ``| sh`` as dangerous
        indirection while the benign viewers (``| tail`` / ``| less``) are NOT,
        so 'benign continuation' is not a blanket pipe pass;
      * the >=2-destructive compound contrast — a genuine multi-destructive chain
        is still classified compound and refused even with a token;
      * the wrong-target / privileged-flag-absent negatives — a continuation can
        never authorize a MISMATCHED target or a token missing a bound flag.
    """

    # The benign continuation family: pipe-to-viewer, output redirect,
    # and background / benign chain. Each is appended to a single destructive op;
    # none is a second destructive leg.
    _BENIGN_CONTINUATIONS = [
        "| tail", "| head", "| grep merged", "| cat", "| wc -l",
        "| tee /tmp/out", "| less",
        "> out.log", "2>&1", "&> out.log",
        "&", "&& echo done", "; echo done",
    ]

    # All recognized destructive ops: (label, base destructive command, mint
    # context). The read side AUTHORIZES the continued command for EVERY one —
    # the force-push / branch-delete target parsers are now continuation-tolerant
    # (they truncate at the first benign terminator before counting positionals),
    # so they join merge / close in this guarantee. ``close`` carries
    # ``--delete-branch`` (its danger trigger), so its token context MUST bind
    # that flag — the read-side set-equality flag gate refuses otherwise — exactly
    # as the mint side extracts it from the approval text. ``merge`` /
    # ``force-push`` / ``branch-delete`` carry no privileged flag.
    _AUTHORIZED_OPS = [
        ("merge", "gh pr merge 5", {"operation_type": "merge", "pr_number": "5"}),
        (
            "close",
            "gh pr close 7 --delete-branch",
            {"operation_type": "close", "pr_number": "7",
             "bound_flags": ["--delete-branch"]},
        ),
        (
            "force-push",
            "git push --force origin main",
            {"operation_type": "force-push", "target_ref": "main"},
        ),
        (
            "branch-delete",
            "git branch -D victim",
            {"operation_type": "branch-delete", "branch": "victim"},
        ),
    ]

    @pytest.mark.parametrize("cont", _BENIGN_CONTINUATIONS)
    @pytest.mark.parametrize("op_label,base,ctx", _AUTHORIZED_OPS)
    def test_single_destructive_op_continuation_authorizes(
        self, tmp_path, op_label, base, ctx, cont
    ):
        """A single destructive op + a benign continuation mints AND authorizes.

        One destructive leg (dangerous, NOT compound); the single-op approval
        token authorizes the continued command. Holds for EVERY recognized op
        (merge / close / force-push / branch-delete) — the guaranteed idiom.
        """
        from merge_guard_post import write_token
        from merge_guard_pre import (
            check_merge_authorization,
            is_compound_destructive_command,
            is_dangerous_command,
        )

        cmd = f"{base} {cont}"
        assert is_dangerous_command(cmd) is True
        assert is_compound_destructive_command(cmd) is False
        assert write_token(dict(ctx), token_dir=tmp_path) is not None
        assert check_merge_authorization(cmd, token_dir=tmp_path) is None

    @pytest.mark.parametrize(
        "cmd",
        ["gh pr merge 5 | bash", "gh pr merge 5 | sh"],
    )
    def test_pipe_to_shell_is_dangerous_indirection_not_benign_viewer(self, cmd):
        """Non-vacuity boundary: a pipe to a SHELL is dangerous indirection, NOT a
        benign viewer. ``_has_pipe_to_shell`` recognizes ``| bash`` / ``| sh`` and
        the command stays is_dangerous — so 'benign continuation' is bounded, not a
        blanket pipe pass.
        """
        from merge_guard_pre import is_dangerous_command
        from shared.merge_guard_common import _has_pipe_to_shell

        assert _has_pipe_to_shell(cmd) is True
        assert is_dangerous_command(cmd) is True

    @pytest.mark.parametrize("viewer", ["| tail", "| less", "| grep merged"])
    def test_benign_viewers_are_not_pipe_to_shell(self, viewer):
        """Companion to the boundary: the benign viewers are NOT classified as
        pipe-to-shell. Together with the prior test this proves the boundary
        DISCRIMINATES (viewers False, shells True) rather than passing everything.
        """
        from shared.merge_guard_common import _has_pipe_to_shell

        assert _has_pipe_to_shell(f"gh pr merge 5 {viewer}") is False

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh pr merge 5 && gh pr close 6 --delete-branch",  # two recognized gh-destructive legs
            "gh pr merge 5 && rm -rf /tmp/x",                  # gh-destructive + rm-head leg
        ],
    )
    def test_two_destructive_compound_still_refused(self, tmp_path, cmd):
        """Non-vacuity contrast: a genuine >=2-destructive compound is still
        is_compound_destructive_command=True and REFUSED via the compound gate even
        with a valid token for the headline op. Proves the >=2-narrowing that lets
        arm (a) through has NOT become a blanket pass.

        (Note: ``gh pr close <N>`` WITHOUT ``--delete-branch`` is not destructive,
        so the second leg must carry its danger trigger to be a genuine compound.)
        """
        from merge_guard_post import write_token
        from merge_guard_pre import (
            check_merge_authorization,
            is_compound_destructive_command,
        )

        assert is_compound_destructive_command(cmd) is True
        write_token(
            {"operation_type": "merge", "pr_number": "5"}, token_dir=tmp_path
        )
        result = check_merge_authorization(cmd, token_dir=tmp_path)
        assert result is not None
        assert "Compound destructive" in result

    # A few representative continuation forms (one per family: pipe-to-viewer,
    # benign-chain, output-redirect) for the negative refuse-direction asserts
    # below — the point is to pin the refuse UNDER a continuation, not to re-sweep
    # the whole family.
    _REFUSE_CONTINUATIONS = ["| tail", "&& echo done", "> out.log"]

    @pytest.mark.parametrize("cont", _REFUSE_CONTINUATIONS)
    def test_wrong_pr_under_continuation_refuses(self, tmp_path, cont):
        """UNDER-BLOCK GUARD: arm-(a)'s authorize is target-BOUND, not a blanket
        continuation pass. A token approved for ONE PR must NOT authorize a
        DIFFERENT PR's merge even with a benign continuation appended — the read
        side re-derives the PR-number target from the command and the mismatch
        refuses.

        Non-vacuity (verified by in-memory mutation, reported in the HANDOFF, not
        encoded here): if the read side dropped the PR-number equality check, this
        wrong-PR command would AUTHORIZE and this assert would flip RED.
        """
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        # Approval is for PR 5; the executed command targets PR 99.
        write_token({"operation_type": "merge", "pr_number": "5"}, token_dir=tmp_path)
        cmd = f"gh pr merge 99 {cont}"
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None

    @pytest.mark.parametrize("cont", _REFUSE_CONTINUATIONS)
    def test_privileged_flag_absent_from_token_under_continuation_refuses(
        self, tmp_path, cont
    ):
        """UNDER-BLOCK GUARD: the privileged-flag bind survives a benign
        continuation. A `gh pr close <N> --delete-branch` command must NOT be
        authorized by a token whose context lacks that bound flag, even with a
        benign continuation appended. op-type and PR number match here, so the
        ONLY axis that differs is the bound flag — a continuation must not erode
        the never-escalate flag bind.

        Non-vacuity (verified by in-memory mutation, reported in the HANDOFF, not
        encoded here): if the read side dropped the bound-flag set-equality check,
        this command would AUTHORIZE on the flag-less token and this assert would
        flip RED.
        """
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        # Token for the close op WITHOUT the --delete-branch bound flag.
        write_token({"operation_type": "close", "pr_number": "7"}, token_dir=tmp_path)
        cmd = f"gh pr close 7 --delete-branch {cont}"
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None


class TestEvalHeredocRejection:
    """Pin the eval+heredoc detection that closes the strip-pipeline-bypass class.

    The strip pipeline removes heredoc bodies BEFORE the regex-match phase.
    An eval-wrapped destructive command inside a heredoc body becomes
    invisible to DANGEROUS_PATTERNS by the time matching runs. Treat
    eval+heredoc shapes as categorically dangerous via a pre-strip check.
    """

    def test_eval_dollar_paren_heredoc_denied(self, tmp_path):
        """eval $(cat <<HEREDOC ... HEREDOC) form denied as dangerous."""
        from merge_guard_pre import is_dangerous_command

        cmd = "eval $(cat <<HEREDOC\ngh pr merge 999 --admin\nHEREDOC\n)"
        assert is_dangerous_command(cmd) is True

    def test_eval_backtick_heredoc_denied(self, tmp_path):
        """eval `cat <<HEREDOC ... HEREDOC` (legacy backtick form) denied."""
        from merge_guard_pre import is_dangerous_command

        cmd = "eval `cat <<HEREDOC\ngh pr merge 999\nHEREDOC\n`"
        assert is_dangerous_command(cmd) is True

    def test_eval_without_heredoc_not_flagged_by_helper(self):
        """Plain eval (no heredoc) is not caught by the eval+heredoc helper.

        A plain ``eval "$VAR"`` may still be dangerous via other paths
        (the eval-or-source variable-expansion guards in
        ``_strip_non_executable_content``), but the categorical
        eval+heredoc helper specifically targets the strip-pipeline
        bypass class. Pin the helper's narrow scope.
        """
        from merge_guard_pre import _has_eval_with_heredoc

        assert _has_eval_with_heredoc('eval "$VAR"') is False
        assert _has_eval_with_heredoc("eval $(echo hi)") is False  # $() but no heredoc
        assert _has_eval_with_heredoc("cat <<EOF\nhi\nEOF") is False  # heredoc but no eval

    def test_eval_heredoc_routes_through_check_authorization(self, tmp_path):
        """End-to-end: eval+heredoc with no token is denied via standard flow."""
        from merge_guard_pre import check_merge_authorization

        cmd = "eval $(cat <<HEREDOC\ngh pr merge 999 --admin\nHEREDOC\n)"
        result = check_merge_authorization(cmd, token_dir=tmp_path)
        assert result is not None, "eval+heredoc must be denied without token"


class TestCrossOperationAuthorizationDenied:
    """Pin the F-1 cross-operation authorization closure.

    Cycle-2 added force-push and branch-delete to extract_context() (the
    write side). Cycle-3 mirrors the symmetric coverage in
    detect_command_operation_type (the read side) and tightens the
    cross-op comparison so a typed token CANNOT authorize a command of a
    different operation class. Pre-fix, the read-side detector returned
    None for force-push/branch-delete, and the comparison fell through
    permissively (cmd_op_type=None → no comparison), allowing any typed
    token to authorize any destructive command in those classes.
    """

    def test_merge_token_does_not_authorize_force_push(self, tmp_path):
        """The empirical bypass: merge-token + force-push command → deny.

        Pre-fix: cmd_op_type was None for `git push --force`, so the
        token's `operation_type=merge` comparison was skipped, and the
        merge token authorized the force-push. Post-fix: cmd_op_type is
        `force-push`, the comparison fires, and the typed-mismatch denies.
        """
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "merge", "pr_number": "100"}, token_dir=tmp_path)
        result = check_merge_authorization(
            "git push --force origin main", token_dir=tmp_path
        )
        assert result is not None
        assert "does not match" in result.lower() or "approval" in result.lower()

    def test_force_push_token_does_not_authorize_merge(self, tmp_path):
        """Symmetric inverse: force-push token + gh pr merge → deny."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "force-push"}, token_dir=tmp_path)
        result = check_merge_authorization(
            "gh pr merge 42 --squash", token_dir=tmp_path
        )
        assert result is not None
        assert "does not match" in result.lower() or "approval" in result.lower()

    def test_branch_delete_token_does_not_authorize_close(self, tmp_path):
        """branch-delete token + gh pr close → deny.

        gh pr close (without --delete-branch) is the close op-class;
        a branch-delete token cannot authorize it.
        """
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "branch-delete"}, token_dir=tmp_path)
        # bare close is not a DANGEROUS_PATTERNS hit; use --delete-branch
        # which IS dangerous and is classified as "close" per the
        # extract_context precedence. The token op_type=branch-delete
        # mismatches the cmd op_type=close.
        result = check_merge_authorization(
            "gh pr close 42 --delete-branch", token_dir=tmp_path
        )
        assert result is not None
        assert "does not match" in result.lower() or "approval" in result.lower()

    def test_unknown_op_command_with_typed_token_denied(self, tmp_path):
        """Typed token + dangerous-but-unrecognized command shape → deny.

        The strict-match semantic: if the token has an operation_type but
        the command's shape doesn't match any of the known op classes, the
        comparison fails closed rather than falling through permissively.
        """
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        # gh api PATCH on /merge endpoint is dangerous (DANGEROUS_PATTERNS L142)
        # but my detector classifies it as None (only git/refs API forms map
        # to force-push/branch-delete). Typed token vs untyped cmd → deny.
        result = check_merge_authorization(
            "gh api -X PATCH repos/o/r/merges -f base=main -f head=feature",
            token_dir=tmp_path,
        )
        assert result is not None

    def test_matching_op_type_still_authorizes(self, tmp_path):
        """Regression-protection: matching op_type still allows execution."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "force-push", "target_ref": "feature"},
                    token_dir=tmp_path)
        result = check_merge_authorization(
            "git push --force origin feature", token_dir=tmp_path
        )
        assert result is None, "matching force-push token must authorize git push --force"


class TestExtractPRNumberLongFlagValueGuard:
    """Pin the F3 long-flag-value defensive check.

    `_GH_PR_NUMBER_RE` matches `gh pr merge --max-retries 5 --auto` and
    captures `5` as the PR number — but `5` is the value of `--max-retries`,
    not a positional. The wrapper `_extract_pr_number()` rejects digits
    that are immediately preceded by a long-form flag, returning None
    (which the caller treats as ambiguous-permissive on the pr_number
    axis). No current `gh pr merge|close` flag takes a digit value, so
    the realistic risk is theoretical — this is defense-in-depth post the
    cycle-3 strict-match enforcement.
    """

    def test_long_flag_numeric_value_not_captured(self):
        """`--max-retries 5 --auto`: `5` is the flag's value, not a PR number."""
        from merge_guard_pre import _extract_pr_number

        cmd = "gh pr merge --max-retries 5 --auto"
        assert _extract_pr_number(cmd) is None

    def test_long_flag_then_real_positional_captures_positional(self):
        """`--max-retries 5 999`: `999` is the real positional, captured correctly."""
        from merge_guard_pre import _extract_pr_number

        cmd = "gh pr merge --max-retries 5 999 --squash"
        assert _extract_pr_number(cmd) == "999"

    def test_value_less_long_flag_before_positional_captures_positional(self):
        """`--auto 999`: `--auto` is value-less, so `999` IS the positional.

        The allowlist `_GH_PR_VALUE_TAKING_FLAGS` excludes value-less
        flags like `--admin`, `--auto`, `--squash`. A digit immediately
        following one of those flags IS the PR positional and is
        captured correctly.
        """
        from merge_guard_pre import _extract_pr_number

        cmd = "gh pr merge --auto 999"
        assert _extract_pr_number(cmd) == "999"

    def test_admin_flag_then_positional_unchanged(self):
        """Regression-protection: `--admin 99` still captures 99 (--admin is value-less)."""
        from merge_guard_pre import _extract_pr_number

        assert _extract_pr_number("gh pr merge --admin 99") == "99"

    def test_known_value_taking_flag_blocks_capture(self):
        """`--body 123 --auto`: `--body` IS in the allowlist; `123` rejected."""
        from merge_guard_pre import _extract_pr_number

        cmd = "gh pr merge --body 123 --auto"
        assert _extract_pr_number(cmd) is None

    def test_subject_flag_blocks_capture(self):
        """`--subject 999`: `--subject` is in the allowlist."""
        from merge_guard_pre import _extract_pr_number

        cmd = "gh pr merge --subject 999"
        assert _extract_pr_number(cmd) is None

    def test_positional_then_long_flag_with_numeric_value_captures_positional(self):
        """`999 --max-retries 5`: `999` is the positional (matched first)."""
        from merge_guard_pre import _extract_pr_number

        cmd = "gh pr merge 999 --max-retries 5"
        assert _extract_pr_number(cmd) == "999"

    def test_short_flag_with_numeric_value_not_blocked(self):
        """Short flags (`-n 5`) are not checked by the defensive guard.

        The guard is narrowly scoped to long-form flags. Short flags are
        combinable and rarely take numeric values; if a real `gh` short
        flag emerges that takes a digit value, this test pins the
        current narrow-scope behavior so any future widening is
        reviewed in lockstep.
        """
        from merge_guard_pre import _extract_pr_number

        cmd = "gh pr merge -n 5 999"
        # 999 is captured as the positional; the regex's _GH_FLAG_TOKENS
        # walk consumed `-n 5` as a flag pair, leaving 999 to capture.
        assert _extract_pr_number(cmd) == "999"

    def test_simple_positional_unchanged(self):
        """Regression-protection: simple `gh pr merge 999` still works."""
        from merge_guard_pre import _extract_pr_number

        assert _extract_pr_number("gh pr merge 999 --squash") == "999"

    def test_no_pr_match_returns_none(self):
        """No PR-number match → None (regression-protection)."""
        from merge_guard_pre import _extract_pr_number

        assert _extract_pr_number("ls -la") is None
        assert _extract_pr_number("gh pr merge --auto") is None  # no positional


# (removed class TestSymmetryContract — superseded by the command-anchored
#  bidirectional suite in test_merge_guard_auth_symmetry.py;
#  it exercised the dropped prose classifier extract_context.)

class TestConcurrentConsumptionThreaded:
    """Real-thread race tests discharging the architect's per-slot O_EXCL
    atomicity proof (#720 Bug C).

    The verification-test suite simulates concurrency via back-to-back
    synchronous calls. These tests use threading.Barrier to release N
    real threads simultaneously at the os.open(O_CREAT|O_EXCL) syscall.
    Python's GIL serializes bytecode, but the syscall releases the GIL,
    so the kernel-level race window is genuine.

    The architect §4.3 invariants under verification:
    - Each concurrent invocation claims a distinct slot.
    - No two invocations claim the same slot.
    - Total successful claims ≤ MAX_USES.
    - Terminal rename happens at most once.
    """

    def _drain_token(self, token_dir, pr_number):
        """Helper: write a fresh token, return its path."""
        from merge_guard_post import write_token

        return write_token({"operation_type": "merge", "pr_number": pr_number}, token_dir=token_dir)

    def test_two_threads_race_on_n2_token_distinct_slots(self, tmp_path):
        """2 threads racing on a N=2 token: each claims a distinct slot.

        Architect §4.3 proof discharge — explicit threading race rather
        than synchronous back-to-back calls.
        """
        import threading

        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_pre import _consume_token

        token_path = self._drain_token(tmp_path, "42")

        results = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()  # release both threads at the same instant
            results.append(_consume_token(token_path))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Both threads succeeded
        assert results.count(True) == 2
        assert results.count(False) == 0

        # Each slot was claimed exactly once (distinct slots)
        assert Path(token_path + USE_MARKER_SUFFIX + "1").exists()
        assert Path(token_path + USE_MARKER_SUFFIX + "2").exists()

        # Terminal rename happened
        assert not Path(token_path).exists()
        assert Path(token_path + ".consumed").exists()

    def test_three_threads_race_on_n2_token_one_denied(self, tmp_path):
        """3 threads racing on N=2 token: 2 win distinct slots, 1 denied.

        Pins the over-subscription failure mode: budget is hard-bounded;
        excess concurrent claimants get False, not phantom slot-3.
        """
        import threading

        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_pre import _consume_token

        token_path = self._drain_token(tmp_path, "42")

        results = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(3)

        def worker():
            barrier.wait()
            outcome = _consume_token(token_path)
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly 2 wins, 1 denial
        assert results.count(True) == 2
        assert results.count(False) == 1

        # Only the 2 valid slot markers exist; no phantom slot-3
        assert Path(token_path + USE_MARKER_SUFFIX + "1").exists()
        assert Path(token_path + USE_MARKER_SUFFIX + "2").exists()
        assert not Path(token_path + USE_MARKER_SUFFIX + "3").exists()
        assert Path(token_path + ".consumed").exists()

    def test_high_contention_invariants_hold(self, tmp_path):
        """8 threads contending for a N=2 token under barrier-release.

        Stress test for the GIL-vs-syscall race envelope. Invariants:
        - Total wins == MAX_USES.
        - Marker file count == MAX_USES.
        - No duplicate slot ownership (each marker has the correct slot
          number recorded in its body).
        """
        import threading

        from shared.merge_guard_common import MAX_USES, USE_MARKER_SUFFIX
        from merge_guard_pre import _consume_token

        token_path = self._drain_token(tmp_path, "42")

        results = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            outcome = _consume_token(token_path)
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly MAX_USES wins; the rest denied
        assert results.count(True) == MAX_USES
        assert results.count(False) == 8 - MAX_USES

        # Exactly MAX_USES marker files; one per slot
        markers = sorted(tmp_path.glob(f"merge-authorized-*{USE_MARKER_SUFFIX}*"))
        assert len(markers) == MAX_USES

        # Each marker body records its own slot — no duplicate ownership
        recorded_slots = set()
        for marker in markers:
            data = json.loads(marker.read_text())
            recorded_slots.add(data["slot"])
        assert recorded_slots == set(range(1, MAX_USES + 1))

    def test_subprocess_race_fs_level_atomicity(self, tmp_path):
        """subprocess.Popen race: spawn 4 OS processes that hit the
        kernel-level race window simultaneously, no GIL involvement.

        Backstop for the in-process threading test in case GIL scheduling
        ever masks a real race condition in the threaded path.
        """
        import subprocess
        import textwrap

        from shared.merge_guard_common import MAX_USES, USE_MARKER_SUFFIX
        from merge_guard_post import write_token

        token_path = write_token({"operation_type": "merge", "pr_number": "99"}, token_dir=tmp_path)
        hooks_dir = Path(__file__).parent.parent / "hooks"

        # Use a filesystem barrier: each child polls for a "go" file before
        # invoking _consume_token, so all 4 cross the os.open boundary
        # within microseconds of each other.
        go_file = tmp_path / "GO"
        script = textwrap.dedent(f"""
            import sys, os, time
            sys.path.insert(0, {str(hooks_dir)!r})
            # Wait for the barrier file
            while not os.path.exists({str(go_file)!r}):
                time.sleep(0.001)
            from merge_guard_pre import _consume_token
            outcome = _consume_token({str(token_path)!r})
            sys.exit(0 if outcome else 1)
        """)
        script_file = tmp_path / "_worker.py"
        script_file.write_text(script)

        procs = [
            subprocess.Popen(
                [sys.executable, str(script_file)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(4)
        ]

        # Release the barrier
        go_file.write_text("go")

        return_codes = [p.wait(timeout=10) for p in procs]
        wins = return_codes.count(0)
        losses = return_codes.count(1)

        # Exactly MAX_USES processes won
        assert wins == MAX_USES, f"expected {MAX_USES} wins, got {wins} (losses={losses})"
        assert losses == 4 - MAX_USES

        # Exactly MAX_USES marker files
        markers = list(tmp_path.glob(f"merge-authorized-*{USE_MARKER_SUFFIX}*"))
        assert len(markers) == MAX_USES


class TestLegacyTokenBoundary:
    """Adversarial coverage for the legacy-token (max_uses missing) path
    (#720 Bug C, backend-coder uncertainty item #1).

    The verification-tests pin individual legacy behaviors. These tests
    cover the boundary cases: legacy tokens approaching TTL, legacy +
    N=2 tokens coexisting in the same session, and the end-to-end
    behavior of legacy tokens against check_merge_authorization (not
    just the _consume_token unit surface).
    """

    def _write_legacy_token(self, tmp_path, pr_number="42", ttl_offset=300):
        """Write a legacy token with no max_uses field; offset controls TTL."""
        now = time.time()
        token_path = tmp_path / f"merge-authorized-legacy-{pr_number}"
        token_path.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + ttl_offset,
            "context": {"pr_number": pr_number, "operation_type": "merge"},
        }))
        return str(token_path)

    def test_legacy_token_blocks_second_command_end_to_end(self, tmp_path):
        """Legacy token authorizes 1 command via check_merge_authorization;
        second identical command is blocked (no N-use budget for legacy)."""
        from merge_guard_pre import check_merge_authorization

        self._write_legacy_token(tmp_path, "42")

        # First: allowed
        assert check_merge_authorization("gh pr merge 42", token_dir=tmp_path) is None

        # Second: blocked — no remaining token (legacy single-use exhausted)
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "AskUserQuestion" in result

    def test_legacy_and_n_use_tokens_coexist(self, tmp_path):
        """Mixed session: legacy single-use token + N=2 token both present.

        Authorize each with a context-matching command. Verifies the N-use
        logic in _consume_token correctly dispatches based on max_uses
        field presence per token, not based on which token was found first.
        """
        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        legacy_path = self._write_legacy_token(tmp_path, "100")
        n_use_path = write_token({"operation_type": "merge", "pr_number": "200"}, token_dir=tmp_path)

        # Consume each token directly via _consume_token to bypass the
        # token-vs-command matching (which is orthogonal to legacy/N-use
        # dispatch). Behavior under test: each token's consumption semantic
        # follows its OWN schema, not whichever was found first by glob.
        assert _consume_token(legacy_path) is True
        assert _consume_token(n_use_path) is True

        # Legacy: renamed directly to .consumed (no slot markers)
        assert not Path(legacy_path).exists()
        assert Path(legacy_path + ".consumed").exists()
        assert not Path(legacy_path + USE_MARKER_SUFFIX + "1").exists()

        # N-use: slot 1 claimed, token still on disk (budget remaining)
        assert Path(n_use_path).exists()
        assert Path(n_use_path + USE_MARKER_SUFFIX + "1").exists()
        assert not Path(n_use_path + ".consumed").exists()

    def test_legacy_token_near_ttl_boundary(self, tmp_path):
        """Legacy token at 1s before expiry: authorizes; at 1s past expiry:
        cleaned up by find_valid_token, command blocked."""
        from merge_guard_pre import check_merge_authorization

        # 1s before TTL expiry: still valid
        token_path = self._write_legacy_token(tmp_path, "42", ttl_offset=1)
        assert check_merge_authorization("gh pr merge 42", token_dir=tmp_path) is None

        # Force the next token to be already expired
        now = time.time()
        expired_path = tmp_path / "merge-authorized-legacy-expired"
        expired_path.write_text(json.dumps({
            "created_at": now - 600,
            "expires_at": now - 1,
            "context": {"pr_number": "43", "operation_type": "merge"},
        }))
        result = check_merge_authorization("gh pr merge 43", token_dir=tmp_path)
        assert result is not None
        # Expired token cleaned up by find_valid_token
        assert not expired_path.exists()

    def test_legacy_token_with_partial_n_use_fields_treated_as_legacy(self, tmp_path):
        """Token has max_uses field but not uses_remaining (malformed-ish):
        max_uses drives behavior — N=2 means slot-marker path is taken.

        Pins the field-driven dispatch: _consume_token reads max_uses, not
        uses_remaining. Defensive coverage for any future write_token path
        that might emit one without the other.
        """
        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_pre import _consume_token

        now = time.time()
        token_path = tmp_path / "merge-authorized-partial-1"
        token_path.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"pr_number": "55"},
            "max_uses": 2,
            # uses_remaining intentionally omitted
        }))

        # max_uses=2 → slot-marker path
        assert _consume_token(str(token_path)) is True
        assert Path(str(token_path) + USE_MARKER_SUFFIX + "1").exists()
        assert not Path(str(token_path) + ".consumed").exists()


class TestRmAsDestructiveLeg:
    """User-ratified rm-as-destructive-leg (#46): an `rm` head-token counts as a
    destructive leg in the COMPOUND count, so a gh/git-destructive op chained with
    `rm` is >=2-destructive → refuse. This does NOT widen the single-command gate:
    `rm` ALONE (or a pure rm&&rm chain with no gh/git op) is is_dangerous=False — the
    merge guard ignores arbitrary `rm` (no scope creep), and obfuscated `rm` spellings
    are NOT chased (no obfuscation-arms-race)."""

    def test_gh_op_chained_with_rm_is_compound(self):
        """`gh pr merge 5 && rm -rf /` — gh-merge + rm = TWO destructive legs → COMPOUND
        → refuse. NON-VACUITY: drop `rm` from the leg set → ONE leg → not compound → RED."""
        from merge_guard_pre import is_compound_destructive_command
        assert is_compound_destructive_command("gh pr merge 5 && rm -rf /") is True

    def test_gh_op_chained_with_benign_is_not_compound(self):
        """`gh pr merge 5 && echo ok` — ONE destructive leg (merge) + a benign `echo`
        → NOT compound; the merge mints/gates as a single op (is_dangerous=True). The
        rm-leg rule must not over-fire on a benign continuation."""
        from merge_guard_pre import is_compound_destructive_command, is_dangerous_command
        assert is_compound_destructive_command("gh pr merge 5 && echo ok") is False
        assert is_dangerous_command("gh pr merge 5 && echo ok") is True

    @pytest.mark.parametrize("cmd", ["rm -rf /", "rm -rf node_modules", "rm -rf a && rm -rf b"])
    def test_pure_rm_is_not_gated_no_scope_creep(self, cmd):
        """ANTI-SCOPE-CREEP: `rm` with NO gh/git op is NOT gated — bare `rm` and even a
        pure `rm && rm` chain return is_dangerous=False, so the merge guard never blocks
        arbitrary `rm` (rm only matters as a leg ALONGSIDE a gh/git op). The compound
        PREDICATE may count pure-rm legs, but the gate is is_dangerous, which stays
        False here, so the read floor allows it and no mint is ever involved."""
        from merge_guard_pre import is_dangerous_command
        assert is_dangerous_command(cmd) is False

    @pytest.mark.parametrize("cmd", [
        "gh pr merge 5 && /bin/rm -rf /",     # path-qualified
        "gh pr merge 5 && r''m -rf /",        # quote-concat
        "gh pr merge 5 && $(echo rm) -rf /",  # command-substitution
        "gh pr merge 5 && rmdir /tmp/x",      # different binary
    ])
    def test_obfuscated_rm_is_not_chased(self, cmd):
        """NO-OBFUSCATION-CHASING: only a bare `rm` head-token counts as a destructive
        leg. Obfuscated spellings (path-qualified, quote-concat, command-sub, a
        different binary) are NOT treated as `rm` → ONE destructive leg (the gh op)
        → NOT compound. The gh op itself is still is_dangerous-gated as a single op."""
        from merge_guard_pre import is_compound_destructive_command, is_dangerous_command
        assert is_compound_destructive_command(cmd) is False
        assert is_dangerous_command(cmd) is True  # the gh op leg is still gated


class TestFDRedirectAdversarial:
    """Adversarial extensions to Bug A's FD-redirect negatives (#720 Bug A).

    Backend-coder added 5 negative tests. These extend to compound
    redirect shapes, process substitution, here-strings, and combined
    redirect+chain forms that should each be correctly classified —
    either as compound (real chain) or as not-compound (pure redirect).
    """

    def test_redirect_with_dev_null_input_not_compound(self):
        """`git push origin main > file.log 2>&1 < /dev/null` — three
        redirects, no chain operator."""
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "git push origin main > file.log 2>&1 < /dev/null"
        ) is False

    def test_redirect_then_and_chain_single_destructive_not_compound(self):
        """`git push origin main 2>&1 && echo done` — ONE destructive leg (push-to-main)
        + a benign `echo` → NOT compound under the honest-mistake ≥2 model. The single
        push op stays is_dangerous-gated (the && operator does not make it compound)."""
        from merge_guard_pre import is_compound_destructive_command, is_dangerous_command

        cmd = "git push origin main 2>&1 && echo done"
        assert is_compound_destructive_command(cmd) is False
        assert is_dangerous_command(cmd) is True

    def test_redirect_then_or_chain_single_destructive_not_compound(self):
        """`gh pr merge 100 2>&1 || echo failed` — ONE destructive leg (merge) + a
        benign `echo` → NOT compound (≥2 model). The merge stays is_dangerous-gated."""
        from merge_guard_pre import is_compound_destructive_command, is_dangerous_command

        cmd = "gh pr merge 100 2>&1 || echo failed"
        assert is_compound_destructive_command(cmd) is False
        assert is_dangerous_command(cmd) is True

    def test_process_substitution_output_not_compound(self):
        """`git push origin main > >(tee push.log)` — bash process
        substitution `>(...)`. No chain operator, so not compound.

        Note: the inner `>` of `>(...)` is part of process substitution
        syntax. The compound regex correctly identifies this as not chained.
        """
        from merge_guard_pre import is_compound_destructive_command

        cmd = "git push origin main > >(tee push.log)"
        # Behavior: this should not be flagged as compound (no real chain).
        assert is_compound_destructive_command(cmd) is False

    def test_here_string_input_not_compound(self):
        """`gh pr merge 42 <<< 'yes'` — here-string `<<<`, no chain."""
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command("gh pr merge 42 <<< 'yes'") is False

    def test_combined_fd_redirects_in_sequence_not_compound(self):
        """`gh pr merge 100 0<&-  1>&2  2>output.log` — multiple FD
        redirects in a single command, none of which form a chain."""
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "gh pr merge 100 0<&-  1>&2  2>output.log"
        ) is False

    def test_append_redirect_not_compound(self):
        """`gh pr merge 100 >> output.log 2>&1` — append `>>` redirect."""
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "gh pr merge 100 >> output.log 2>&1"
        ) is False

    def test_redirect_then_semicolon_single_destructive_not_compound(self):
        """`git push --force 2>&1 ; ls` — ONE destructive leg (force-push) + a benign
        `ls` → NOT compound (≥2 model). The force-push stays is_dangerous-gated."""
        from merge_guard_pre import is_compound_destructive_command, is_dangerous_command

        cmd = "git push --force 2>&1 ; ls"
        assert is_compound_destructive_command(cmd) is False
        assert is_dangerous_command(cmd) is True

    def test_spaceless_fd_redirect_then_pipe_two_destructive_is_compound(self):
        """`gh pr merge 100 2>&1|gh pr merge 999 --admin` — TWO destructive legs
        (merge 100 + merge 999 --admin) chained by a real pipe → STILL compound under
        the honest-mistake ≥2 model. The spaceless `2>&1|` FD-tail-then-pipe must not
        slip past detection (the FD pre-strip neutralizes `2>&1`, the surviving `|`
        splits two destructive legs)."""
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "gh pr merge 100 2>&1|gh pr merge 999 --admin"
        ) is True

    def test_spaceless_fd_redirect_then_pipe_force_push_single_not_compound(self):
        """`git push --force 2>&1|cat` — ONE destructive leg (force-push) + a benign
        `cat` → NOT compound (≥2 model). The force-push stays is_dangerous-gated."""
        from merge_guard_pre import is_compound_destructive_command, is_dangerous_command

        cmd = "git push --force 2>&1|cat"
        assert is_compound_destructive_command(cmd) is False
        assert is_dangerous_command(cmd) is True

    def test_spaceless_fd_redirect_then_pipe_branch_delete_with_rm_is_compound(self):
        """`git branch -D foo 2>&1|rm -rf ~` — branch-delete (gh/git-destructive) piped
        to `rm -rf` (a destructive head-token). Under the FINAL model (user-ratified
        rm-as-destructive-leg), the FD pre-strip's length-preserving offset fix slices
        the legs correctly and the two destructive legs (branch-delete + rm) → ≥2 →
        COMPOUND → refuse. NON-VACUITY: drop `rm` from the destructive-leg set → ONE
        leg → not compound → flips RED."""
        from merge_guard_pre import is_compound_destructive_command, is_dangerous_command

        cmd = "git branch -D foo 2>&1|rm -rf ~"
        assert is_compound_destructive_command(cmd) is True
        assert is_dangerous_command(cmd) is True

    # --- Class A bypass-regression pins (#723 cycle 1 remediation) ---
    #
    # Class A: digit-then-pipe with no FD redirect. Pre-fix lookbehind
    # `(?<![0-9>])` suppressed the bare-`|` match whenever the preceding
    # character was a digit — including the trailing digit of a PR-number
    # positional. Post-fix simplified regex has no lookbehind, so the
    # bare-`|` matches unconditionally. Pre-strip is a no-op on Class-A
    # inputs (no FD-redirect token to strip).

    def test_class_a_digit_then_pipe_gh_pr_merge_is_compound(self):
        """Class A — `gh pr merge 100|gh pr merge 999` — no FD redirect;
        trailing PR-number digit immediately followed by bare pipe to a
        second destructive command. Pre-fix bypass."""
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "gh pr merge 100|gh pr merge 999"
        ) is True

    def test_class_a_digit_then_pipe_force_push_with_rm_is_compound(self):
        """Class A — `git push --force 100|rm -rf ~` — force-push (gh/git-destructive)
        piped to `rm -rf` (a destructive head-token). Under the FINAL model (user-
        ratified rm-as-destructive-leg), the two destructive legs (force-push + rm) →
        ≥2 → COMPOUND → refuse. The trailing PR-number digit `100` adjacent to `|` does
        not suppress the split. NON-VACUITY: drop `rm` from the destructive-leg set →
        ONE leg → not compound → flips RED."""
        from merge_guard_pre import is_compound_destructive_command, is_dangerous_command

        cmd = "git push --force 100|rm -rf ~"
        assert is_compound_destructive_command(cmd) is True
        assert is_dangerous_command(cmd) is True

    def test_class_a_alpha_then_pipe_branch_delete_single_not_compound(self):
        """Class A variant — `git branch -D foo|cat` — ONE destructive leg
        (branch-delete) + a benign `cat` → NOT compound (≥2 model). The branch-delete
        stays is_dangerous-gated."""
        from merge_guard_pre import is_compound_destructive_command, is_dangerous_command

        cmd = "git branch -D foo|cat"
        assert is_compound_destructive_command(cmd) is False
        assert is_dangerous_command(cmd) is True

    # --- Class B variant pins (#723 cycle 1 remediation) ---

    def test_class_b_reverse_fd_redirect_then_pipe_single_not_compound(self):
        """Class B variant — `git push --force 1>&2|cat` — reverse FD direction
        (`1>&2`). ONE destructive leg (force-push) + a benign `cat` → NOT compound
        (≥2 model). The force-push stays is_dangerous-gated; the FD pre-strip still
        correctly neutralizes the reverse `1>&2` so it is not mistaken for a leg."""
        from merge_guard_pre import is_compound_destructive_command, is_dangerous_command

        cmd = "git push --force 1>&2|cat"
        assert is_compound_destructive_command(cmd) is False
        assert is_dangerous_command(cmd) is True

    def test_fd_to_file_redirect_alone_not_compound(self):
        """Negative regression pin — `cat 1>file 2>&1` — has an FD-to-FD
        redirect (`2>&1`) and a write redirect (`1>file`) but NO chain
        operator. Must remain not-compound. Verifies the strip pattern
        does NOT over-match `1>file` (no `&` after the `>`)."""
        from merge_guard_pre import is_compound_destructive_command

        assert is_compound_destructive_command(
            "cat 1>file 2>&1"
        ) is False


class TestSymmetryAdversarial:
    """Adversarial Bug B symmetry cases — multi-quoted-region prose,
    mistyped commands, defensive empty/None inputs (#720 Bug B).

    These extend the verification-tests' 5 happy-path symmetry pairings
    with surfaces the orchestrator's question-generation behavior could
    realistically produce: multiple backticked tokens, typos, and
    edge-case empty prose.
    """

    # (removed 7 methods that exercised the dropped prose extractor
    #  merge_guard_post.extract_context — multi-quoted-region precedence, the
    #  typo→keyword-ladder fallback, empty/whitespace/no-keyword prose, and the
    #  prose ladder-reorder. The command-anchored model (locate_command_regions +
    #  extract_command_context) is covered by the bidirectional suite in
    #  test_merge_guard_auth_symmetry.py; classifier edge inputs stay below.)

    def test_none_safe_classifier(self):
        """detect_command_operation_type on edge inputs: empty string
        and short non-command-shape strings return None."""
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type("") is None
        assert detect_command_operation_type("xyz") is None
        assert detect_command_operation_type("git status") is None


class TestAuditEmitFormatAdversarial:
    """Bug C audit-emit format invariance under adversarial conditions
    (#720 Bug C, backend-coder open question #3 implicit).

    Verification-tests pin the format under the default MAX_USES. These
    tests cover format invariance under unicode basenames, when MAX_USES
    is monkeypatched to other integers, and the absence of audit emit
    on the unhappy paths.
    """

    def test_audit_format_includes_token_basename(self, tmp_path, capfd):
        """Audit line includes the os.path.basename(token_path) literally."""
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        token_path = write_token({"operation_type": "merge", "pr_number": "7"}, token_dir=tmp_path)
        capfd.readouterr()  # drain write_token's emit

        _consume_token(token_path)
        out = capfd.readouterr()
        assert os.path.basename(token_path) in out.err
        # And the path is NOT echoed in full — only the basename.
        # (Defensive: avoid leaking the full home-dir path to stderr.)
        assert str(tmp_path) not in out.err

    def test_audit_format_under_monkeypatched_max_uses(self, tmp_path, capfd, monkeypatch):
        """If MAX_USES is monkeypatched to 5, the format reads `slot N/5`.

        Pins the format-by-substitution contract: the emitter reads
        the live max_uses value at consume-time, not a hard-coded constant.

        Note: write_token captures MAX_USES at module-import via
        `from shared.merge_guard_common import MAX_USES`, so we must reload
        merge_guard_post AFTER patching. The reload is restored on teardown
        to avoid polluting the module state for downstream tests.
        """
        import importlib

        import merge_guard_post
        import shared.merge_guard_common as common

        # Save the live module state for teardown
        _saved_max_uses = common.MAX_USES

        # Monkeypatch BEFORE write_token so the token records max_uses=5
        monkeypatch.setattr(common, "MAX_USES", 5)
        importlib.reload(merge_guard_post)

        try:
            from merge_guard_post import write_token

            token_path = write_token({"operation_type": "merge", "pr_number": "13"}, token_dir=tmp_path)
            # Confirm token records max_uses=5
            token_data = json.loads(Path(token_path).read_text())
            assert token_data["max_uses"] == 5

            capfd.readouterr()
            from merge_guard_pre import _consume_token
            _consume_token(token_path)
            out = capfd.readouterr()
            assert "(slot 1/5)" in out.err
        finally:
            # Restore module state so downstream tests see MAX_USES=2
            common.MAX_USES = _saved_max_uses
            importlib.reload(merge_guard_post)

    def test_no_audit_emit_on_legacy_path(self, tmp_path, capfd):
        """Legacy tokens (no max_uses field) use the rename-only path —
        backend-coder's design emits audit on that path too. Pin actual
        behavior so accidental changes flag the test.
        """
        from merge_guard_pre import _consume_token

        now = time.time()
        token_path = tmp_path / "merge-authorized-legacy-7"
        token_path.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"pr_number": "7"},
        }))

        capfd.readouterr()
        result = _consume_token(str(token_path))
        out = capfd.readouterr()

        assert result is True
        # Legacy path: per the implementation it does not emit a slot/MAX
        # line because that line is inside the N-use branch only.
        assert "(slot " not in out.err

    def test_audit_basename_with_special_chars_safe(self, tmp_path, capfd):
        """Token basename containing special chars (hyphen, digits) is
        emitted verbatim. Pins that the emitter doesn't quote/escape."""
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        token_path = write_token(
            {"operation_type": "merge", "pr_number": "123"},
            token_dir=tmp_path,
        )
        capfd.readouterr()
        _consume_token(token_path)
        out = capfd.readouterr()

        basename = os.path.basename(token_path)
        # Basename appears in the audit line as-is
        assert basename in out.err
        # And the line is a single [security] entry on consume
        consume_lines = [l for l in out.err.splitlines()
                         if "[security] merge-authorized token consumed" in l]
        assert len(consume_lines) == 1


class TestEnvelopeIntegration:
    """Envelope-level (PreToolUse stdin → main() → JSON stdout)
    integration tests for the 3 bug surfaces (#720).

    Verification-tests target the check_merge_authorization function
    directly. These supplement with full main()-shape invocations to
    catch any drift between the function-level contract and the JSON
    serialization layer at the hook's external boundary.

    Scope: 3-5 envelope tests, one per bug surface plus the end-to-end
    flow. Not exhaustive — that's the function-level layer's job.
    """

    def _pre_envelope(self, command: str) -> str:
        """Build a PreToolUse stdin envelope (Bash matcher)."""
        return json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": "test-envelope-session",
        })

    def _post_envelope(self, question: str, answer: str = "yes",
                       options: list | None = None) -> str:
        """Build a PostToolUse stdin envelope (AskUserQuestion matcher). Post-#32
        the mint is OPTION-ANCHORED, so callers pass an `options` list with the
        command in the clicked option's description + an `answer` matching that
        option's label."""
        q: dict = {"question": question}
        if options is not None:
            q["options"] = options
        return json.dumps({
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [q]},
            "tool_response": {"answers": {question: answer}},
            "session_id": "test-envelope-session",
        })

    def _invoke_pre(self, command: str, tmp_path):
        """Invoke merge_guard_pre.main() with a built envelope.
        Returns (exit_code, stdout_text, stderr_text)."""
        from merge_guard_pre import main as pre_main

        envelope = self._pre_envelope(command)
        stdout_buf = io.StringIO()
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)), \
             patch("sys.stdout", stdout_buf):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        return exc_info.value.code, stdout_buf.getvalue()

    def _invoke_post(self, question: str, tmp_path, answer: str = "yes",
                     options: list | None = None):
        """Invoke merge_guard_post.main() with a built envelope."""
        from merge_guard_post import main as post_main

        envelope = self._post_envelope(question, answer, options)
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                post_main()
        return exc_info.value.code

    def test_bug_a_envelope_fd_redirect_authorizes(self, tmp_path):
        """Full envelope: Bug A surface, post over-block-tokenizer fix. The approval
        mints a push-to-main token, and `git push origin main 2>&1` now AUTHORIZES:
        `_extract_force_push_target_ref` truncates at the `2>&1` redirect before the
        positional count, so the destination re-derives to `main` and matches the
        token. The redirect filename is structurally outside the positional window,
        so this is a faithful-click authorize, NOT a #1032 under-block.
        """
        # Step 1: approve via post hook — option-anchored (#32): the command
        # lives in the CLICKED option's description.
        post_code = self._invoke_post(
            "Authorize the force-push?", tmp_path, answer="Yes, force-push",
            options=[
                {"label": "Yes, force-push", "description": "Run `git push origin main`"},
                {"label": "Cancel", "description": "Abort"},
            ],
        )
        assert post_code == 0
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1

        # Step 2: run the FD-redirect command through pre hook → now AUTHORIZES
        # (the redirect is truncated before the 2-positional ref parse, so the
        # destination re-derives to `main` and matches the minted token).
        pre_code, _pre_stdout = self._invoke_pre(
            "git push origin main 2>&1", tmp_path
        )
        assert pre_code == 0  # Authorized (faithful click; redirect re-derived)

    def test_bug_b_envelope_joes_bare_push_authorizes(self, tmp_path):
        """Full envelope: Bug B surface. AskUserQuestion question with quoted-command
        `git push origin main` (Joe's case) → token writes push-to-main op_type (GAP3:
        a PLAIN push to main is a DISTINCT op from --force force-push); pre hook
        recognizes bare `git push origin main` as push-to-main and authorizes."""
        post_code = self._invoke_post(
            "Confirm the push to main?", tmp_path, answer="Yes, push",
            options=[
                {"label": "Yes, push", "description": "Run `git push origin main`"},
                {"label": "Cancel", "description": "Abort"},
            ],
        )
        assert post_code == 0

        # Verify token has push-to-main op_type (symmetric classifier worked)
        token_files = list(tmp_path.glob("merge-authorized-*"))
        assert len(token_files) == 1
        token_data = json.loads(token_files[0].read_text())
        assert token_data["context"]["operation_type"] == "push-to-main"

        # Run the matching command — should be authorized
        pre_code, pre_stdout = self._invoke_pre(
            "git push origin main", tmp_path
        )
        assert pre_code == 0
        assert '"permissionDecision": "deny"' not in pre_stdout

    def test_bug_c_envelope_two_uses_then_third_denied(self, tmp_path):
        """Full envelope: Bug C surface. Write token, consume 2 uses via
        pre-main(), assert third consume is denied at the JSON envelope
        layer with the AskUserQuestion deny reason."""
        post_code = self._invoke_post(
            "Confirm the merge?", tmp_path, answer="Yes, merge",
            options=[
                {"label": "Yes, merge", "description": "Run `gh pr merge 42`"},
                {"label": "Cancel", "description": "Abort"},
            ],
        )
        assert post_code == 0

        # 2 successful authorizations (MAX_USES=2)
        code1, _ = self._invoke_pre("gh pr merge 42", tmp_path)
        code2, _ = self._invoke_pre("gh pr merge 42", tmp_path)
        assert code1 == 0
        assert code2 == 0

        # 3rd: denied at envelope layer
        code3, out3 = self._invoke_pre("gh pr merge 42", tmp_path)
        assert code3 == 2  # deny exit code
        deny_payload = json.loads(out3)
        assert deny_payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "AskUserQuestion" in (
            deny_payload["hookSpecificOutput"]["permissionDecisionReason"]
        )

    def test_envelope_ladder_reorder_branch_d_mjs_case(self, tmp_path):
        """Full envelope: mj's case, option-anchored (#32). The clicked option
        carries `git branch -D feat/old`; the question prose still contains the
        distractor word 'merged' — the command-anchored classifier picks
        branch-delete from the OPTION's command (not the 'merged' prose), so the
        old ladder-reorder ambiguity cannot mis-mint a merge."""
        post_code = self._invoke_post(
            "Delete the merged feature branch?", tmp_path, answer="Yes, delete",
            options=[
                {"label": "Yes, delete", "description": "Run `git branch -D feat/old`"},
                {"label": "Cancel", "description": "Abort"},
            ],
        )
        assert post_code == 0

        token_files = list(tmp_path.glob("merge-authorized-*"))
        token_data = json.loads(token_files[0].read_text())
        assert token_data["context"]["operation_type"] == "branch-delete"
        assert token_data["context"]["branch"] == "feat/old"

        pre_code, pre_stdout = self._invoke_pre(
            "git branch -D feat/old", tmp_path
        )
        assert pre_code == 0
        assert '"permissionDecision": "deny"' not in pre_stdout

    def test_envelope_deny_emits_well_formed_json(self, tmp_path):
        """Envelope JSON-shape contract: deny path emits a payload with
        hookSpecificOutput.hookEventName="PreToolUse",
        permissionDecision="deny", and a non-empty permissionDecisionReason.

        Catches JSON-serialization drift between the function's string
        return and the wire-format the platform expects.
        """
        pre_code, pre_stdout = self._invoke_pre(
            "gh pr merge 99 --admin", tmp_path
        )
        assert pre_code == 2
        payload = json.loads(pre_stdout)
        assert "hookSpecificOutput" in payload
        hso = payload["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert isinstance(hso["permissionDecisionReason"], str)
        assert hso["permissionDecisionReason"] != ""

    # -----------------------------------------------------------------------
    # Envelope-shape adversarial coverage. The non-envelope tests at lines
    # 462 / 820 / 831 cover the same logical paths via direct main()
    # invocation, but a future restructure of stdin parsing could regress
    # envelope-shape behavior while those tests stay green. These tests
    # pin behavior through the envelope-fixture interface specifically.

    def test_envelope_with_malformed_tool_input_fails_closed(self, tmp_path):
        """tool_input is a non-dict (e.g., a string). Defensive contract:
        hook fails closed via the catch-all exception handler (exit 2),
        does NOT crash, does NOT auto-allow."""
        from merge_guard_pre import main as pre_main

        # tool_input is a string instead of a dict — .get() on a string
        # raises AttributeError → catch-all → fail-closed deny.
        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": "git push origin main",  # type-error shape
            "session_id": "test-envelope-session",
        })
        stdout_buf = io.StringIO()
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)), \
             patch("sys.stdout", stdout_buf):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        assert exc_info.value.code == 2  # fail-closed
        # Output is the well-formed deny payload from the catch-all.
        payload = json.loads(stdout_buf.getvalue())
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_envelope_missing_tool_input_field_handled(self, tmp_path):
        """Envelope omits tool_input entirely. Defensive: hook treats as
        empty command (suppress + exit 0) — does NOT crash, does NOT
        treat absent tool_input as a destructive command."""
        from merge_guard_pre import main as pre_main

        envelope = json.dumps({
            "tool_name": "Bash",
            # tool_input intentionally absent
            "session_id": "test-envelope-session",
        })
        stdout_buf = io.StringIO()
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)), \
             patch("sys.stdout", stdout_buf):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        assert exc_info.value.code == 0  # suppress on empty command
        # Suppress-output, not a deny payload
        assert '"permissionDecision": "deny"' not in stdout_buf.getvalue()

    def test_envelope_with_extra_unknown_fields_ignored(self, tmp_path):
        """Forward-compat: envelope contains unknown extra top-level
        fields. Hook ignores them and processes normally."""
        from merge_guard_pre import main as pre_main

        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 99 --admin"},
            "session_id": "test-envelope-session",
            # Unknown future fields:
            "transcript_path": "/tmp/some-transcript.json",
            "future_feature": {"some": "metadata"},
            "another_unknown_key": [1, 2, 3],
        })
        stdout_buf = io.StringIO()
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)), \
             patch("sys.stdout", stdout_buf):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        # Destructive command without token → deny exit 2 (same as the
        # canonical deny test). Extra fields do not change behavior.
        assert exc_info.value.code == 2
        payload = json.loads(stdout_buf.getvalue())
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_envelope_with_empty_command_suppresses(self, tmp_path):
        """tool_input.command is the empty string. Defensive: suppress
        + exit 0 — does NOT crash, does NOT auto-allow downstream
        destructive checks on an empty command."""
        from merge_guard_pre import main as pre_main

        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": ""},
            "session_id": "test-envelope-session",
        })
        stdout_buf = io.StringIO()
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)), \
             patch("sys.stdout", stdout_buf):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        assert exc_info.value.code == 0
        assert '"permissionDecision": "deny"' not in stdout_buf.getvalue()


class TestLeaseToDefaultGateAndMint:
    """Lease-push-to-default fold + presence-bind (#1064).

    `git push --force-with-lease origin main` was gated by the read floor
    (push-to-main arm accepts any dash token) but excluded from the MINT
    classifier's push-to-main arm by a negative lookahead -> detect=None ->
    unmintable -> a faithful single-command click was PERMANENTLY blocked
    (gated-but-unmintable over-block). The fix removes the mint-arm lookahead
    (flag-walk now byte-identical to the read arm's) and separates plain-push
    vs lease-push token identities via the --force-with-lease PRESENCE bind
    in PRIVILEGED_FLAGS (the close/--delete-branch precedent).

    These tests certify gate+mint symmetry END-TO-END through the real hook
    mains (post mint -> pre authorize/refuse). The #1042 set-equality refusal
    matrix (hand-built token contexts against _token_matches_command) lives in
    test_merge_guard_privileged_flags.py — that suite owns the bind invariant;
    the read-leg refusals here exercise the DIFFERENT envelope layer (minted
    token -> pre main JSON deny), not the same assertion.
    """

    LEASE = "git push --force-with-lease origin main"
    LEASE_VALUE = "git push --force-with-lease=main:abc123 origin main"
    PLAIN = "git push origin main"

    # Envelope drivers are inline (the idiom used by every class outside
    # TestEnvelopeIntegration); the mint is option-anchored — the command
    # lives in the CLICKED option's description.

    def _mint(self, cmd: str, tmp_path) -> dict:
        """Drive the REAL post hook main() with an approval whose clicked option
        embeds `cmd`; assert exactly one token minted; return its context."""
        from merge_guard_post import main as post_main

        envelope = json.dumps({
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{
                "question": "Push to the default branch?",
                "options": [
                    {"label": "Yes, push", "description": f"Run `{cmd}`"},
                    {"label": "Cancel", "description": "Abort"},
                ],
            }]},
            "tool_response": {"answers": {"Push to the default branch?": "Yes, push"}},
            "session_id": "test-lease-session",
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                post_main()
        assert exc_info.value.code == 0
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1, "approval did not mint exactly one token"
        return json.loads(tokens[0].read_text())["context"]

    def _pre(self, cmd: str, tmp_path):
        """Run `cmd` through the REAL pre hook main(); return (exit_code, stdout)."""
        from merge_guard_pre import main as pre_main

        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": cmd},
            "session_id": "test-lease-session",
        })
        stdout_buf = io.StringIO()
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)), \
             patch("sys.stdout", stdout_buf):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        return exc_info.value.code, stdout_buf.getvalue()

    @pytest.mark.parametrize("cmd", [
        "git push --force-with-lease origin main",
        "git push --force-with-lease origin master",
        "git push --force-with-lease=main:abc123 origin main",
    ], ids=["bare-main", "bare-master", "equals-value-main"])
    def test_lease_to_default_detects_as_push_to_main(self, cmd):
        from shared.merge_guard_common import detect_command_operation_type

        assert detect_command_operation_type(cmd) == "push-to-main"

    def test_lease_to_feature_branch_stays_unrecognized(self):
        """Contrast control: the fold widens ONLY the default-branch arm — a lease
        push to a topic branch is still unrecognized AND ungated (must not widen)."""
        from shared.merge_guard_common import (
            detect_command_operation_type,
            is_dangerous_command,
        )

        assert detect_command_operation_type(
            "git push --force-with-lease origin feature/x") is None
        assert not is_dangerous_command("git push --force-with-lease origin feature/x")

    def test_lease_approval_mints_and_authorizes_byte_identical(self, tmp_path):
        """The #1064 DENY->ALLOW flip: a faithful lease click mints a push-to-main
        token carrying the presence bind, and the byte-identical execution
        AUTHORIZES (this exact flow was permanently blocked pre-fix)."""
        ctx = self._mint(self.LEASE, tmp_path)
        assert ctx["operation_type"] == "push-to-main"
        assert ctx["target_ref"] == "main"
        assert ctx["bound_flags"] == ["--force-with-lease"]
        code, _out = self._pre(self.LEASE, tmp_path)
        assert code == 0

    def test_lease_equals_value_approval_mints_and_authorizes_byte_identical(
            self, tmp_path):
        """The =<ref>:<expect> spelling rides the fix: same op-class, same canonical
        bare bound flag (the boolean bind drops the inline value), byte-identical
        approve/execute authorizes."""
        ctx = self._mint(self.LEASE_VALUE, tmp_path)
        assert ctx["operation_type"] == "push-to-main"
        assert ctx["target_ref"] == "main"
        assert ctx["bound_flags"] == ["--force-with-lease"]
        code, _out = self._pre(self.LEASE_VALUE, tmp_path)
        assert code == 0

    def test_plain_approval_does_not_authorize_lease_execution(self, tmp_path):
        """Approve a PLAIN push to main, execute the LEASE form -> DENY: the lease
        push CAN rewrite history, so the minted plain token (bound_flags=[]) must
        not set-equal the executed {--force-with-lease}."""
        ctx = self._mint(self.PLAIN, tmp_path)
        assert ctx["bound_flags"] == []
        code, out = self._pre(self.LEASE, tmp_path)
        assert code == 2
        assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_lease_approval_does_not_authorize_plain_execution(self, tmp_path):
        """The symmetric direction: approve LEASE, execute PLAIN -> DENY (set-
        equality refuses dropped flags too; the operator re-approves)."""
        ctx = self._mint(self.LEASE, tmp_path)
        assert ctx["bound_flags"] == ["--force-with-lease"]
        code, out = self._pre(self.PLAIN, tmp_path)
        assert code == 2
        assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


# =============================================================================
# Layer 4 (#797): TestTokenLifecycleInvariants
#
# Pins the 5 token-lifecycle invariants documented in
# shared/merge_guard_common.py module docstring. Each invariant has one
# alias test (I-1 through I-5) plus supporting structural pins for the
# NEW Layer 1/3/5 code paths.
#
# Counter-test cardinality target: {14 RED} on full Layer 1/3/5 revert.
# Each test carries an inline `# counter-test:` mutation recipe per
# CLAUDE.md PR #697 / §13.8 discipline so the load-bearingness of each
# pin is independently verifiable by a reviewer.
# =============================================================================


class TestTokenLifecycleInvariants:
    """Pin the 5 token-lifecycle invariants + new Layer 1/3/5 code paths.

    Real-fixture discipline: every test uses tmp_path (NOT mocked file
    I/O) so the on-disk lifecycle is exercised end-to-end.
    """

    # ------------------------------------------------------------------
    # Invariant alias tests (5)
    # ------------------------------------------------------------------

    def test_i1_at_most_one_unused_token(self, tmp_path):
        """I-1: at most one unused token at any time (Layer 5).

        Two write_token calls — the second invokes cleanup_unused_tokens
        before O_EXCL, retiring the first. Exactly one unused token
        remains; the other is .consumed.

        # counter-test: comment out _cleanup_unused_tokens(token_dir) in
        #               merge_guard_post.write_token before O_EXCL → two
        #               unused tokens coexist; len(unused) == 2 → RED.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        with patch("merge_guard_post.time") as mock_time:
            mock_time.time.return_value = time.time() + 1
            write_token({"operation_type": "merge"}, token_dir=tmp_path)

        all_files = list(tmp_path.glob("merge-authorized-*"))
        unused = [
            t for t in all_files
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(unused) == 1, f"I-1 violated: {len(unused)} unused tokens"

    def test_i2_successful_merge_immediately_retires_token(self, tmp_path):
        """I-2: successful gh pr merge retires the consuming token (Layer 1).

        Drive merge_guard_post.main() with a Bash envelope shaped like
        a successful gh pr merge. The unused token is retired to
        .consumed regardless of remaining MAX_USES slots.

        # counter-test: replace `_retire_token_for_command(command)` in
        #               merge_guard_post.main() Bash branch with `pass`
        #               → token stays unused; .consumed never created.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token, main

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        before_unused = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(before_unused) == 1

        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42 --squash"},
            "tool_response": {
                "stdout": "Merged pull request #42",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        after_unused = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        after_consumed = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(after_unused) == 0, "I-2 violated: token not retired"
        assert len(after_consumed) == 1, "I-2 violated: expected exactly one .consumed"

    def test_i3_ttl_expiry_retires_token(self, tmp_path):
        """I-3: expired tokens are removed by find_valid_token (Layer 2 audit).

        Write a token with expires_at in the past; find_valid_token
        sees it and unlinks via _safe_remove.

        # counter-test: change `if expires_at < now:` to `if False:` at
        #               merge_guard_pre.find_valid_token → expired
        #               tokens persist on disk indefinitely.
        # expected RED cardinality: {1}
        """
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now - 600,
            "expires_at": now - 1,  # Expired
            "context": {"operation_type": "merge"},
        }
        expired_path = tmp_path / "merge-authorized-99999"
        expired_path.write_text(json.dumps(token_data))

        valid_token, valid_path = find_valid_token(token_dir=tmp_path)
        assert valid_token is None
        assert valid_path is None
        # Expired token was removed by _safe_remove
        assert not expired_path.exists()

    def test_i4_failed_operation_preserves_token_for_retry(self, tmp_path):
        """I-4: failed merge preserves token for retry up to MAX_USES.

        After a single _consume_token call against a fresh MAX_USES=2
        token, the token still exists (only one slot claimed). A
        second identical-context retry within TTL still authorizes.

        # counter-test: change `if slot == max_uses:` to `if True:` in
        #               merge_guard_pre._consume_token → first attempt
        #               consumes; retry blocked even within MAX_USES.
        # expected RED cardinality: {1}
        """
        from shared.merge_guard_common import MAX_USES
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        assert MAX_USES >= 2, "test assumes MAX_USES >= 2"
        write_token({"operation_type": "merge", "pr_number": "1"}, token_dir=tmp_path)

        # First call: authorized (slot 1)
        assert check_merge_authorization("gh pr merge 1", token_dir=tmp_path) is None
        # Token still present (not terminally consumed yet)
        unused = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(unused) == 1, "I-4 violated: token retired before MAX_USES"

        # Second call: still authorized (slot 2 = final)
        assert check_merge_authorization("gh pr merge 1", token_dir=tmp_path) is None

    def test_i5_cross_session_token_rejected(self, tmp_path):
        """I-5: tokens from foreign sessions are rejected (existing scope check).

        Write a token tagged with a foreign session_id; query
        find_valid_token with a different current_session — token is
        skipped (not used).

        # counter-test: remove the `current_session != token_session`
        #               check at merge_guard_pre.find_valid_token →
        #               foreign-session token would authorize.
        # expected RED cardinality: {1}
        """
        from merge_guard_pre import find_valid_token

        now = time.time()
        foreign = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {"operation_type": "merge"},
            "session_id": "foreign-session-xyz",
            "max_uses": 2,
            "uses_remaining": 2,
        }
        (tmp_path / "merge-authorized-foreign").write_text(json.dumps(foreign))

        with patch("merge_guard_pre.get_session_id", return_value="local-session-abc"):
            valid_token, valid_path = find_valid_token(token_dir=tmp_path)
        assert valid_token is None
        assert valid_path is None
        # Token NOT cleaned up — still valid for its own session
        assert (tmp_path / "merge-authorized-foreign").exists()

    # ------------------------------------------------------------------
    # Layer 5 supporting pins (3)
    # ------------------------------------------------------------------

    def test_cleanup_unused_tokens_skips_use_n_markers(self, tmp_path):
        """Layer 5: cleanup_unused_tokens preserves .use-N markers.

        # counter-test: remove `if USE_MARKER_SUFFIX in basename:
        #               continue` in cleanup_unused_tokens → marker
        #               renamed to .consumed; audit trail lost.
        # expected RED cardinality: {1}
        """
        from shared.merge_guard_common import cleanup_unused_tokens

        marker = tmp_path / "merge-authorized-1234.use-1"
        marker.write_text('{"claim_time": 1234567890}')

        cleanup_unused_tokens(tmp_path)

        # Marker is preserved (skip-filter respected)
        assert marker.exists()
        # No .consumed sibling created from the marker
        assert not (tmp_path / "merge-authorized-1234.use-1.consumed").exists()

    def test_cleanup_unused_tokens_skips_already_consumed(self, tmp_path):
        """Layer 5: cleanup_unused_tokens is a no-op on .consumed tokens.

        # counter-test: remove `if path.endswith(".consumed"): continue`
        #               → double-rename creates .consumed.consumed shape.
        # expected RED cardinality: {1}
        """
        from shared.merge_guard_common import cleanup_unused_tokens

        consumed = tmp_path / "merge-authorized-1234.consumed"
        consumed.write_text('{"context": {}}')

        cleanup_unused_tokens(tmp_path)

        # Still .consumed, NOT .consumed.consumed
        assert consumed.exists()
        assert not (tmp_path / "merge-authorized-1234.consumed.consumed").exists()

    def test_cleanup_unused_tokens_handles_concurrent_consume(self, tmp_path):
        """Layer 5: race-safe against concurrent retirement.

        Simulate FileNotFoundError from os.rename (file already moved
        by concurrent path) — helper must swallow, not raise.

        # counter-test: remove the try/except wrapping os.rename in
        #               cleanup_unused_tokens → FileNotFoundError
        #               escapes; caller (write_token) breaks.
        # expected RED cardinality: {1}
        """
        from shared.merge_guard_common import cleanup_unused_tokens

        token = tmp_path / "merge-authorized-1234"
        token.write_text('{"context": {}}')

        original_rename = os.rename

        def racing_rename(src, dst):
            # Delete the source between glob() and rename() to simulate
            # a concurrent winner.
            try:
                os.unlink(src)
            except OSError:
                pass
            original_rename(src, dst)  # Now raises FileNotFoundError

        with patch("shared.merge_guard_common.os.rename", side_effect=racing_rename):
            # Must NOT raise
            cleanup_unused_tokens(tmp_path)

    # ------------------------------------------------------------------
    # Layer 3 supporting pins (4)
    # ------------------------------------------------------------------

    def test_cleanup_orphan_tokens_respects_max_age(self, tmp_path):
        """Layer 3: tokens older than max_age_seconds are reaped.

        # counter-test: change comparator `now - mtime > max_age_seconds`
        #               to `now - mtime > max_age_seconds * 1000` →
        #               threshold becomes ~1000h; stale token survives.
        # expected RED cardinality: {1}
        """
        from shared.merge_guard_common import cleanup_orphan_tokens

        token = tmp_path / "merge-authorized-stale"
        token.write_text('{"context": {}}')
        # Backdate mtime to 2h ago (> 1h threshold)
        old_time = time.time() - 7200
        os.utime(token, (old_time, old_time))

        # 3600 is an explicit 1h threshold, intentionally decoupled from the ORPHAN
        # default (12*TOKEN_TTL); exercises reaper behavior at a chosen value, not the default.
        cleanup_orphan_tokens(tmp_path, max_age_seconds=3600)

        assert not token.exists()

    def test_cleanup_orphan_tokens_skips_recent(self, tmp_path):
        """Layer 3: recent unused tokens are preserved.

        # counter-test: change the comparator to `now - mtime > 0` →
        #               every token (even fresh) reaped.
        # expected RED cardinality: {1}
        """
        from shared.merge_guard_common import cleanup_orphan_tokens

        token = tmp_path / "merge-authorized-fresh"
        token.write_text('{"context": {}}')
        # Default mtime is "just now"

        # 3600 is an explicit 1h threshold, intentionally decoupled from the ORPHAN
        # default (12*TOKEN_TTL); exercises reaper behavior at a chosen value, not the default.
        cleanup_orphan_tokens(tmp_path, max_age_seconds=3600)

        assert token.exists()

    def test_cleanup_orphan_tokens_skips_consumed_and_markers(self, tmp_path):
        """Layer 3: skip-filters preserve .consumed and .use-N markers.

        # counter-test: remove either skip-filter in cleanup_orphan_tokens
        #               → consumed file or marker is unlinked despite
        #               living in the cleanup_consumed_tokens domain.
        # expected RED cardinality: {1}
        """
        from shared.merge_guard_common import cleanup_orphan_tokens

        consumed = tmp_path / "merge-authorized-1234.consumed"
        marker = tmp_path / "merge-authorized-1234.use-1"
        consumed.write_text('{"context": {}}')
        marker.write_text('{"claim_time": 1234567890}')
        # Backdate both well past threshold
        old_time = time.time() - 7200
        for p in (consumed, marker):
            os.utime(p, (old_time, old_time))

        # 3600 is an explicit 1h threshold, intentionally decoupled from the ORPHAN
        # default (12*TOKEN_TTL); exercises reaper behavior at a chosen value, not the default.
        cleanup_orphan_tokens(tmp_path, max_age_seconds=3600)

        # Both preserved — these live in cleanup_consumed_tokens' domain
        assert consumed.exists()
        assert marker.exists()

    def test_find_valid_token_invokes_orphan_cleanup(self, tmp_path):
        """Layer 3 primary trigger: find_valid_token calls
        cleanup_orphan_tokens adjacent to cleanup_consumed_tokens.

        # counter-test: remove the cleanup_orphan_tokens call site at
        #               merge_guard_pre.find_valid_token → primary
        #               trigger removed; orphans survive every
        #               dangerous-Bash precheck.
        # expected RED cardinality: {1}
        """
        from merge_guard_pre import find_valid_token

        with patch(
            "merge_guard_pre._cleanup_orphan_tokens",
        ) as mock_cleanup:
            find_valid_token(token_dir=tmp_path)
        mock_cleanup.assert_called_once_with(tmp_path)

    # ------------------------------------------------------------------
    # Layer 1 supporting pins (2)
    # ------------------------------------------------------------------

    def test_post_main_bash_branch_no_op_on_non_merge_command(self, tmp_path):
        """Layer 1 Block-1 filter: non-merge commands are no-op.

        Fixture forces Block 1 to be the LOAD-BEARING discriminator: the
        command is non-merge (`git log`) but the stdout contains "Merged
        pull request" (realistic — git log of a merge commit). Block 3
        stdout-pattern would NOT reject (substring present), so the only
        thing keeping retirement from firing is Block 1's op-type filter.

        # counter-test: change `!= "merge"` to `== "merge"` in Block 1
        #               of the Bash branch → git-log invocation falls
        #               through Blocks 1-3 and retires the token
        #               (false-positive retirement).
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token, main

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "git log --oneline -5"},
            "tool_response": {
                # Realistic git log of a merge commit — Block 3 substring
                # IS present, so Block 1 is forced to be the discriminator.
                "stdout": "abc1234 Merged pull request #42 from feat/x",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        unused = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(unused) == 1, "Layer 1 Block-1 violated: non-merge retired token"

    def test_post_main_bash_branch_no_op_on_interrupted_merge(self, tmp_path):
        """Layer 1 Block-2 filter: interrupted=True is no-op.

        Feed a Bash payload that LOOKS like a successful merge
        (stdout matches) but with interrupted=True. The Block 2
        filter must reject, preserving the token.

        # counter-test: remove the `interrupted is True` check in
        #               Block 2 → interrupted merge retires the
        #               token (false-positive retirement).
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token, main

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42 --squash"},
            "tool_response": {
                "stdout": "Merged pull request #42",
                "stderr": "",
                "interrupted": True,  # User interrupted
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        unused = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(unused) == 1, (
            "Layer 1 Block-2 violated: interrupted merge retired token"
        )


# =============================================================================
# Layer 4 EXTENSIONS (#797): TestTokenLifecycleExtensions
#
# Phantom-green probes, audit-anchor structural pins, false-positive
# discriminators, envelope-divergence detection, deeper cross-session and
# retry regression coverage, and concurrent-race adversarial scenarios.
#
# Counter-test cardinality target on full Layer 1/3/5 revert combined with
# TestTokenLifecycleInvariants: aggregate scales to {14 base + this class
# additions} on full revert. Each test carries an inline `# counter-test:`
# mutation recipe with measured cardinality from the TEST-phase probe
# campaign (see HANDOFF for full data).
#
# Phantom-green discoveries codified here (from probe data):
#   - I-3 alias does NOT pin orphan-cleanup mechanism (only TTL-expiry).
#     New test pins orphan-mechanism MECHANISM independently.
#   - 2 Layer 1 supporting negative tests pass-for-wrong-reason under
#     full Bash-branch revert. New sentinel adds positive-discriminator
#     contrast so revert produces RED cardinality > 1.
#   - Layer 5 mechanism-no-op probe: 3 of 4 helper supporting tests
#     stay GREEN when helper is gutted (they pin "what NOT to happen").
#     New test exercises the integrated callsite via write_token so
#     mechanism revert is detected.
# =============================================================================


class TestTokenLifecycleExtensions:
    """Extension coverage for #797 token-lifecycle hardening.

    Complementary to TestTokenLifecycleInvariants — pins surfaces that the
    base invariant aliases leave exposed:
      - structural audit anchors (docstring labels, counter-test counts)
      - phantom-green discriminators (orphan-mechanism, retire-mechanism)
      - false-positive sentinels (dry-run, command-shape boundary)
      - envelope-divergence (string, empty-dict, None-stdout shapes)
      - deeper cross-session + retry-preservation regression
      - concurrent races on shared token-dir state

    Real-fixture discipline preserved (tmp_path; mocking limited to
    time/session_id/TOKEN_DIR + controlled os.rename interpose).
    """

    # ------------------------------------------------------------------
    # Audit-anchor structural pins (scope item 6)
    # ------------------------------------------------------------------

    def test_module_docstring_pins_five_invariant_sections(self):
        """5 invariant section headers (I-1..I-5) in merge_guard_common.py.

        Uses ANCHORED substring per CLAUDE.md "Coupling-via-substring-count"
        — bare label `I-1` returns >1 due to cross-reference prose
        (e.g., "Maintains invariant I-1"). The full section-header form is
        unique per label and pins the docstring contract directly.

        # counter-test: rename I-1 section header in merge_guard_common.py
        #               module docstring (e.g., "I-1 (at most one" → "I-A
        #               (at most one") → assertion FAILS on the I-1 line.
        # expected RED cardinality: {1}
        """
        from pathlib import Path

        common_path = (
            Path(__file__).resolve().parent.parent
            / "hooks" / "shared" / "merge_guard_common.py"
        )
        src = common_path.read_text()
        anchors = [
            "I-1 (at most one unused token at any time):",
            "I-2 (successful operation immediately retires the token):",
            "I-3 (TTL expiry retires the token):",
            "I-4 (failed operation preserves token for retry within TTL up to MAX_USES):",
            "I-5 (cross-session tokens never valid):",
        ]
        for anchor in anchors:
            count = src.count(anchor)
            assert count == 1, (
                f"audit-anchor drift: expected exactly 1 occurrence of "
                f"{anchor!r}; got {count}"
            )

    def test_invariant_class_pins_fourteen_counter_test_comments(self):
        """TestTokenLifecycleInvariants class carries exactly 14 inline
        `# counter-test:` mutation recipes — one per invariant + supporting
        pin. Scoped via class-marker-to-EOF or next-class extraction so the
        17-file-total hit count (3 hits outside the class) doesn't pollute.

        # counter-test: delete a `# counter-test:` comment from any test
        #               method body in TestTokenLifecycleInvariants → count
        #               drops to 13; assertion FAILS.
        # expected RED cardinality: {1}
        """
        from pathlib import Path

        test_path = Path(__file__).resolve()
        src = test_path.read_text()
        marker = "class TestTokenLifecycleInvariants:"
        next_class_marker = "class TestTokenLifecycleExtensions:"
        start = src.find(marker)
        end = src.find(next_class_marker)
        assert start != -1, "TestTokenLifecycleInvariants class not found"
        assert end != -1 and end > start, "boundary class missing"
        section = src[start:end]
        # Indent-anchored: counter-test recipes inside docstring bodies of
        # test methods are 8-space-indented. Banner/header prose mentions
        # of "# counter-test:" use 0-space indent and are excluded.
        count = sum(
            1 for line in section.splitlines()
            if line.startswith("        # counter-test:")
        )
        assert count == 14, (
            f"counter-test discipline drift: expected 14 indent-anchored "
            f"recipes in TestTokenLifecycleInvariants; got {count}"
        )

    # ------------------------------------------------------------------
    # Duplicate-class shadow audit (scope item 9 / memory 631891d7)
    # ------------------------------------------------------------------

    def test_no_duplicate_token_lifecycle_class(self):
        """grep -E '^class TestTokenLifecycle' returns exactly 2 classes —
        the base TestTokenLifecycleInvariants + this Extensions class.
        Per memory `631891d7`, accidental class shadowing in pytest silently
        masks the second class.

        # counter-test: introduce `class TestTokenLifecycleInvariants:` as
        #               a duplicate elsewhere in the file → count rises
        #               to 3; assertion FAILS.
        # expected RED cardinality: {1}
        """
        from pathlib import Path
        import re

        test_path = Path(__file__).resolve()
        src = test_path.read_text()
        matches = re.findall(r"^class TestTokenLifecycle\w+:", src, re.MULTILINE)
        # Expect: TestTokenLifecycleInvariants + TestTokenLifecycleExtensions
        assert len(matches) == 2, (
            f"duplicate-class shadow: expected 2 TestTokenLifecycle* classes; "
            f"got {len(matches)} ({matches})"
        )

    # ------------------------------------------------------------------
    # Phantom-green discriminator: orphan-cleanup MECHANISM (scope item 1)
    # ------------------------------------------------------------------

    def test_find_valid_token_actually_reaps_stale_orphan(self, tmp_path):
        """Layer 3 mechanism pin (closes I-3 alias phantom-green gap).

        TEST-phase probe finding: mutating the orphan-cleanup comparator
        `now - mtime > max_age_seconds` to `<` causes test_i4 and test_i5
        to flip RED (orphan-cleanup reaps fresh tokens before they're
        scoped), BUT the I-3 alias stays GREEN — because I-3 tests
        TTL-expiry via `expires_at`, not the orphan-mechanism mtime check.

        This test pins the MECHANISM contract directly: a token older than
        ORPHAN_TOKEN_MAX_AGE_SECONDS is unlinked from disk when
        find_valid_token runs, even if its `expires_at` would otherwise
        keep it alive.

        # counter-test: remove the cleanup_orphan_tokens call site at
        #               merge_guard_pre.find_valid_token OR flip its
        #               comparator to `<` → stale-mtime token survives
        #               the find_valid_token call.
        # expected RED cardinality: {1}
        """
        from shared.merge_guard_common import ORPHAN_TOKEN_MAX_AGE_SECONDS
        from merge_guard_pre import find_valid_token

        now = time.time()
        # Token whose expires_at is FUTURE (would survive I-3) but whose
        # mtime is past the orphan threshold (so orphan-cleanup reaps it).
        future_expiry = now + 999999
        token_data = {
            "created_at": now - ORPHAN_TOKEN_MAX_AGE_SECONDS - 100,
            "expires_at": future_expiry,
            "context": {"operation_type": "merge"},
        }
        stale = tmp_path / "merge-authorized-stale-mech"
        stale.write_text(json.dumps(token_data))
        # Backdate mtime past the threshold
        old_time = now - ORPHAN_TOKEN_MAX_AGE_SECONDS - 100
        os.utime(stale, (old_time, old_time))

        find_valid_token(token_dir=tmp_path)

        # Orphan-cleanup MECHANISM should have unlinked it. If only I-3
        # (TTL-expiry) were active, the future expires_at would keep it.
        assert not stale.exists(), (
            "phantom-green probe: orphan-cleanup MECHANISM not exercised; "
            "I-3 alias passes but mtime-based reap is unwired"
        )

    # ------------------------------------------------------------------
    # Phantom-green discriminator: Layer 5 mechanism (integrated callsite)
    # ------------------------------------------------------------------

    def test_write_token_actually_retires_prior_unused(self, tmp_path):
        """Layer 5 mechanism pin (closes Layer 5 supporting-test
        phantom-green gap).

        TEST-phase probe finding: 3 of 4 cleanup_unused_tokens supporting
        tests stay GREEN when the helper is gutted to no-op — because they
        pin "what NOT to happen" (markers preserved, .consumed not double-
        renamed, race-safety on FileNotFoundError). Only test_i1 catches
        the gutted helper, and only via the integrated callsite.

        This test exercises the integrated callsite explicitly: writing a
        second token via write_token MUST result in the first being
        .consumed-renamed by cleanup_unused_tokens BEFORE the new token
        exists on disk (invariant I-1 mechanism via Layer 5 placement).

        # counter-test: comment out _cleanup_unused_tokens(token_dir) in
        #               merge_guard_post.write_token before O_EXCL OR gut
        #               the helper to return None → assertion FAILS.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        first_unused = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(first_unused) == 1
        first_path = first_unused[0]

        with patch("merge_guard_post.time") as mock_time:
            mock_time.time.return_value = time.time() + 1
            write_token({"operation_type": "merge"}, token_dir=tmp_path)

        # Specifically: the FIRST path should now exist as .consumed (not
        # just "some .consumed somewhere"). This pins the rename target.
        consumed_first = Path(str(first_path) + ".consumed")
        assert consumed_first.exists(), (
            "Layer 5 mechanism not exercised: first token not renamed to "
            ".consumed; phantom-green if asserting only on aggregate count"
        )

    # ------------------------------------------------------------------
    # False-positive sentinel: gh pr merge --dry-run (scope item 7)
    # ------------------------------------------------------------------

    def test_dry_run_merge_does_not_retire_token(self, tmp_path):
        """Layer 1 Block 3 sentinel: `gh pr merge --dry-run` emits
        "would merge" framing, NOT "Merged pull request". The substring
        match must reject this to prevent false-positive retirement on a
        no-op preview command.

        Tests the semantic specificity of the stdout-pattern substring
        beyond what backend-coder's "merge" → "Merged pull request"
        analysis covers. Closes the architect §10 LOW-risk surface.

        # counter-test: change `"Merged pull request" not in stdout_text`
        #               to `"merge" not in stdout_text` (over-broad) →
        #               substring still rejects "would merge" because the
        #               capital-M asymmetry happens to hold, BUT change to
        #               `"would merge" in stdout_text` (inverted) → token
        #               retired falsely. Use single-char `"M"` mutation
        #               for the canonical over-broad probe.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token, main

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42 --squash --dry-run"},
            "tool_response": {
                "stdout": "would merge pull request #42 (dry-run; no changes)",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        unused = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(unused) == 1, (
            "Layer 1 Block-3 violated: --dry-run retired token "
            "(false-positive on 'would merge' stdout framing)"
        )

    def test_block3_over_broad_substring_caught_by_capitalization(self, tmp_path):
        """Layer 1 Block 3 case-sensitivity pin: the stdout substring
        `Merged pull request` is intentionally capitalized. Lowercasing
        the gh CLI output (hypothetical future format change) MUST NOT
        retire the token without a deliberate substring update.

        # counter-test: change `"Merged pull request"` to
        #               `"Merged pull request".lower()` (or use
        #               `stdout_text.lower()` in the comparison) → token
        #               retired on lowercased stdout.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token, main

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42 --squash"},
            "tool_response": {
                # Lowercased (hypothetical gh future-format)
                "stdout": "merged pull request #42",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        unused = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(unused) == 1, (
            "Layer 1 Block-3 case-sensitivity violated: lowercased stdout "
            "retired the token"
        )

    # ------------------------------------------------------------------
    # Envelope-divergence detection (scope item 8 / §13.6)
    # ------------------------------------------------------------------

    def test_block2_rejects_string_shape_tool_response(self, tmp_path):
        """§13.6 envelope-divergence: STRING-shape tool_response (the
        failed-Bash route per Agent SDK) MUST NOT retire the token.

        Pair-revert structure (closes negative-assertion phantom-green
        class per pair-revert discipline pattern):
          Phase 1 — correct dict envelope → assert .consumed APPEARS
                    (positive discriminator; fails if Bash branch absent)
          Phase 2 — string envelope → assert .consumed does NOT appear
                    (negative discriminator; the existing scenario)

        Pair-revert of the entire Bash branch breaks phase 1 — no
        retirement happens for any input — and phase 2 still vacuously
        passes; pair-revert thus produces RED on the positive half,
        catching the absent-branch regression that the original recipe's
        "discriminative" assertion failed to discriminate.

        Block 2 isinstance guard mutation alone does NOT produce RED
        because the AttributeError-fallback on string.get() produces the
        same observable outcome (exit 0, no .consumed) as the guard.
        This test guards against branch-absent / branch-broken
        regressions, not against guard-only mutations.

        # counter-test: revert the Bash branch in merge_guard_post.main()
        #               to its pre-C4 state (no Bash handling) → phase 1
        #               assertion `.consumed APPEARS` FAILS because no
        #               retirement fires; phase 2 still passes vacuously.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token, main

        # Phase 1 — positive discriminator: correct envelope retires token
        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        positive_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42 --squash"},
            "tool_response": {
                "stdout": "Merged pull request #42",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(positive_envelope)):
            with pytest.raises(SystemExit):
                main()
        phase1_consumed = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(phase1_consumed) == 1, (
            "phase 1 positive discriminator: correct envelope did NOT "
            "retire the token; Bash branch absent or non-functional"
        )

        # Phase 2 — negative: string-shape envelope must NOT retire
        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        unused_before = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(unused_before) == 1
        string_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42"},
            "tool_response": "Error: Exit code 1\nfailed to merge",
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(string_envelope)):
            with pytest.raises(SystemExit):
                main()
        # Fresh token still unused (only the phase-1 token is .consumed)
        unused_after = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(unused_after) == 1, (
            "§13.6 envelope-divergence: string-shape tool_response retired "
            "the token; Block 2 isinstance guard breached"
        )

    def test_block2_rejects_empty_dict_tool_response(self, tmp_path):
        """§13.6 envelope-divergence: empty-dict tool_response MUST NOT
        retire the token (Block 3 stdout-pattern catches missing stdout).

        Pair-revert structure (closes negative-assertion phantom-green):
          Phase 1 — correct dict envelope → assert .consumed APPEARS
          Phase 2 — empty-dict envelope → assert no new .consumed

        Pair-revert of the Bash branch breaks phase 1. Block-3-only
        mutation (`if False:` in stdout check) does not produce RED on
        phase 1 — correct envelope still retires — and phase 2 would
        proceed to retirement, producing RED there. So this pair pin
        detects BOTH absent-branch regression (phase 1) and Block 3
        relaxation (phase 2).

        # counter-test (Bash-branch revert): phase 1 FAILS — no
        #               retirement on correct envelope.
        # counter-test (Block 3 → `if False:`): phase 2 FAILS — empty
        #               dict envelope retires the token.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token, main

        # Phase 1 — positive discriminator
        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        positive_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42 --squash"},
            "tool_response": {
                "stdout": "Merged pull request #42",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(positive_envelope)):
            with pytest.raises(SystemExit):
                main()
        phase1_consumed = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(phase1_consumed) == 1, (
            "phase 1 positive discriminator: correct envelope did NOT "
            "retire the token"
        )

        # Phase 2 — negative: empty-dict envelope must NOT retire
        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        empty_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42"},
            "tool_response": {},
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(empty_envelope)):
            with pytest.raises(SystemExit):
                main()
        unused_after = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(unused_after) == 1, (
            "§13.6 envelope-divergence: empty-dict tool_response retired "
            "the token"
        )

    def test_block3_rejects_none_stdout(self, tmp_path):
        """§13.6 envelope-divergence: malformed dict with stdout=None
        MUST NOT retire the token. The `isinstance(stdout_text, str)`
        guard in Block 3 catches this.

        Pair-revert structure (closes negative-assertion phantom-green):
          Phase 1 — correct dict envelope → assert .consumed APPEARS
          Phase 2 — None-stdout envelope → assert no new .consumed

        Pair-revert of the Bash branch breaks phase 1. Block 3
        isinstance mutation alone does NOT produce RED on phase 2
        because the TypeError fallback still produces exit 0 + no
        .consumed (same as the guard's success path).

        # counter-test (Bash-branch revert): phase 1 FAILS — no
        #               retirement on correct envelope.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token, main

        # Phase 1 — positive discriminator
        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        positive_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42 --squash"},
            "tool_response": {
                "stdout": "Merged pull request #42",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(positive_envelope)):
            with pytest.raises(SystemExit):
                main()
        phase1_consumed = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(phase1_consumed) == 1, (
            "phase 1 positive discriminator: correct envelope did NOT "
            "retire the token"
        )

        # Phase 2 — negative: None-stdout envelope must NOT retire
        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        malformed_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42"},
            "tool_response": {
                "stdout": None,  # Malformed type
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(malformed_envelope)):
            with pytest.raises(SystemExit):
                main()
        unused_after = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(unused_after) == 1, (
            "§13.6 envelope-divergence: None-stdout retired the token; "
            "Block 3 isinstance guard breached"
        )

    # ------------------------------------------------------------------
    # Cross-session deeper coverage via Layer 1 path (scope item 4)
    # ------------------------------------------------------------------

    def test_layer1_bash_branch_skips_foreign_session_token(self, tmp_path):
        """I-5 deeper coverage via Layer 1: a foreign-session token must
        NOT be retired by this session's `gh pr merge` PostToolUse, even
        when the command + stdout would otherwise match.

        The base I-5 alias covers the find_valid_token path; this pins
        the same scope-check inside _retire_token_for_command.

        # counter-test: remove the `current_session != token_session`
        #               check in merge_guard_post._retire_token_for_command
        #               → foreign-session token would be retired by this
        #               session's PostToolUse (cross-session breach).
        # expected RED cardinality: {1}
        """
        from merge_guard_post import main

        # Write a token tagged with a foreign session_id directly
        # (bypass write_token to avoid auto-tagging with current session).
        foreign_session = "foreign-session-xyz"
        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {"operation_type": "merge"},
            "session_id": foreign_session,
            "max_uses": 2,
            "uses_remaining": 2,
        }
        foreign_token = tmp_path / "merge-authorized-foreign-bash"
        foreign_token.write_text(json.dumps(token_data))

        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42 --squash"},
            "tool_response": {
                "stdout": "Merged pull request #42",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("merge_guard_post.get_session_id",
                   return_value="local-session-abc"), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        # Token still unused — foreign-session retire skipped.
        assert foreign_token.exists(), (
            "I-5 violated in Layer 1: foreign-session token retired by "
            "local-session PostToolUse"
        )
        consumed = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(consumed) == 0, (
            "I-5 violated in Layer 1: produced spurious .consumed of foreign "
            "token"
        )

    # ------------------------------------------------------------------
    # Retry-preservation deeper coverage (scope item 5)
    # ------------------------------------------------------------------

    def test_same_context_retry_claims_use_n_slot(self, tmp_path):
        """I-4 deeper: same-context retry within TTL claims a `.use-N`
        slot (per #720 Bug C), NOT a Layer-5 cleanup retirement. The
        retry does NOT consume Layer 5's "retire prior unused" path
        because the first invocation didn't WRITE a new token; it
        consumed a slot of the existing one.

        # counter-test: introduce a Layer-5 cleanup call into
        #               _consume_token's success path → retry retires the
        #               token prematurely; second .use-N slot never claimed.
        # expected RED cardinality: {1}
        """
        from shared.merge_guard_common import MAX_USES, USE_MARKER_SUFFIX
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        assert MAX_USES >= 2
        write_token({"operation_type": "merge", "pr_number": "1"}, token_dir=tmp_path)

        # First retry — claims slot 1
        assert check_merge_authorization("gh pr merge 1", token_dir=tmp_path) is None
        markers_after_1 = list(tmp_path.glob(f"*{USE_MARKER_SUFFIX}*"))
        assert len(markers_after_1) == 1, (
            "I-4 violated: first call didn't claim a .use-1 slot marker"
        )

        # Token NOT terminally retired yet (still has slot 2)
        unused = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed")
            and USE_MARKER_SUFFIX not in t.name
        ]
        assert len(unused) == 1

    def test_cross_context_call_does_not_consume_orig_token_slot(self, tmp_path):
        """I-4 deeper: a DIFFERENT-context command (different op_type or
        different PR number) must NOT consume a slot of the original
        token. Cross-context isolation preserves retry budget for the
        intended context.

        # counter-test: relax the context-matching in
        #               merge_guard_pre._token_matches_command (e.g., make
        #               op_type match alone sufficient regardless of
        #               pr_number) → cross-PR call consumes original
        #               token's slot.
        # expected RED cardinality: {1}
        """
        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        # Write a token authorized for PR #42
        ctx = {"operation_type": "merge", "pr_number": "42"}
        write_token(ctx, token_dir=tmp_path)

        # Cross-context call: different PR — should NOT match token, should
        # be blocked (returns non-None per check_merge_authorization), and
        # CRUCIALLY no use-1 marker should be created against PR 42's token.
        markers_before = list(tmp_path.glob(f"*{USE_MARKER_SUFFIX}*"))
        assert len(markers_before) == 0
        result = check_merge_authorization("gh pr merge 999", token_dir=tmp_path)
        markers_after = list(tmp_path.glob(f"*{USE_MARKER_SUFFIX}*"))

        # Either: (a) cross-PR was blocked AND no marker created OR
        #         (b) cross-PR was authorized via the token (THIS is the
        #             bug case — marker would be created).
        # We pin the no-marker invariant directly.
        assert len(markers_after) == 0, (
            "I-4 cross-context isolation violated: PR-999 call consumed a "
            "slot of the PR-42 token"
        )

    # ------------------------------------------------------------------
    # Concurrent races (scope item 3)
    # ------------------------------------------------------------------

    def test_two_posttooluse_retire_same_token_no_double_consumed(self, tmp_path):
        """Layer 1 concurrent retire: two PostToolUse events arrive for
        the same token (e.g., user re-merges immediately after first
        completion). Both retire-attempts observe the race-recover
        semantics: exactly one .consumed rename wins; no .consumed.consumed
        shape is produced.

        Deterministic via os.rename monkeypatch interpose (per memory
        1727b853 — PR #720 pattern).

        # counter-test: remove the (FileNotFoundError, OSError) handler
        #               around os.rename in _retire_token_for_command →
        #               second-fire raises and observer-except swallows
        #               (but no double .consumed) — test might still pass.
        #               More discriminative: remove the path.endswith(
        #               ".consumed") skip → would create .consumed.consumed.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token, _retire_token_for_command

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        # First retire-attempt — race-recover scenario.
        # SEC-S2: op_type is now a required positional argument.
        first = _retire_token_for_command("gh pr merge 42", "merge", token_dir=tmp_path)
        # Second retire-attempt against the (already-retired) directory.
        second = _retire_token_for_command("gh pr merge 42", "merge", token_dir=tmp_path)

        # First retires; second sees no candidate (the .consumed sibling
        # is skipped by the path.endswith(".consumed") filter) → False.
        assert first is True
        assert second is False, (
            "Concurrent retire-recover: second retire should observe no "
            "candidate; instead it retired again (possible double-rename)"
        )
        # No .consumed.consumed shape exists.
        double = list(tmp_path.glob("*.consumed.consumed"))
        assert len(double) == 0, (
            f"Race-recover semantics violated: produced {double} "
            "(.consumed.consumed shape)"
        )

    def test_layer5_cleanup_recovers_from_concurrent_rename(self, tmp_path):
        """Layer 5 race vs Layer 1 retire: cleanup_unused_tokens runs at
        write_token time; a concurrent _retire_token_for_command may
        race for the same os.rename target. Both paths fail-open via
        (FileNotFoundError, OSError) catches and converge to a consistent
        single-.consumed state.

        Deterministic via os.rename interpose: cleanup's rename target is
        deleted by an "imaginary concurrent winner" between glob and
        rename, simulating the race.

        # counter-test: remove the try/except (FileNotFoundError, OSError)
        #               wrapping os.rename in cleanup_unused_tokens → the
        #               concurrent-rename raises; assertion that
        #               cleanup completes without exception FAILS.
        # expected RED cardinality: {1}
        """
        from shared.merge_guard_common import cleanup_unused_tokens

        token = tmp_path / "merge-authorized-race-1234"
        token.write_text('{"context": {}}')

        # Track rename invocations
        rename_calls = []
        original_rename = os.rename

        def racing_rename(src, dst):
            rename_calls.append((src, dst))
            # Imaginary concurrent winner moves src out from under us
            if os.path.exists(src):
                os.unlink(src)
            original_rename(src, dst)  # Raises FileNotFoundError

        with patch("shared.merge_guard_common.os.rename", side_effect=racing_rename):
            # Must NOT raise — convergence is silent
            cleanup_unused_tokens(tmp_path)

        # Helper attempted the rename once (its only rename call)
        assert len(rename_calls) == 1
        # No .consumed shape created — concurrent winner unlinked it.
        # Token is gone; consistent state reached.
        assert not token.exists()
        assert not (tmp_path / "merge-authorized-race-1234.consumed").exists()

    # ------------------------------------------------------------------
    # Block-1 false-positive sentinel (closes negative-test phantom-green)
    # ------------------------------------------------------------------

    def test_block1_negative_test_paired_with_positive_discriminator(
        self, tmp_path
    ):
        """Layer 1 Block-1 positive+negative discriminator pin (closes
        Layer 1 negative-supporting-test phantom-green gap).

        TEST-phase probe finding: the 2 base Layer 1 negative tests
        (test_post_main_bash_branch_no_op_on_non_merge_command,
        test_post_main_bash_branch_no_op_on_interrupted_merge) stay GREEN
        when the entire Bash branch is reverted, because they pin "no
        retirement" and an absent branch produces "no retirement" for
        the right surface reason but the wrong mechanism reason.

        This test pins BOTH halves in the same fixture: a merge command
        retires; an identical-PostToolUse non-merge command does NOT.
        Pair-revert of the Bash branch breaks the positive half, producing
        the missing RED signal.

        # counter-test: revert the entire Bash branch (Blocks 1-3 +
        #               _retire_token_for_command call) → the merge case
        #               FAILS (positive half) — pair-revert produces RED.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token, main

        # Phase 1: positive — gh pr merge retires the token
        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        merge_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42 --squash"},
            "tool_response": {
                "stdout": "Merged pull request #42",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(merge_envelope)):
            with pytest.raises(SystemExit):
                main()
        consumed_phase1 = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(consumed_phase1) == 1, (
            "Positive discriminator: gh pr merge did NOT retire the token "
            "(Bash branch missing or non-functional)"
        )

        # Phase 2: negative — git status does NOT retire a fresh token
        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        status_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "tool_response": {
                "stdout": "On branch main\nNothing to commit",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(status_envelope)):
            with pytest.raises(SystemExit):
                main()
        unused_phase2 = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(unused_phase2) == 1, (
            "Negative discriminator: git status retired the token "
            "(Block 1 op_type filter regressed)"
        )

    # ------------------------------------------------------------------
    # SEC-S2 op_type symmetry (#797 cycle-2)
    #
    # Extends Layer 1 token retirement from merge-only to op_type-symmetric
    # via LAYER1_SUCCESS_STDOUT_PATTERNS lookup table at
    # shared/merge_guard_common.py. Each new op_type (close, branch-delete,
    # force-push) has a positive test + a pair-revert sentinel per F2
    # phantom-green mitigation discipline. force-push uses Block 3 = None
    # (2-block degradation); its Block 2 structural defense is pinned via
    # the 7th test. The SSOT-pin test (test_sec_s2_lookup_table_ssot_pin)
    # enforces atomic-coupling between table mutations and this test
    # corpus per CLAUDE.md "Coupling-via-substring-count: atomic-commit
    # rule" applied to dict-key cardinality.
    # ------------------------------------------------------------------

    def _write_session_token(self, tmp_path, op_type, session_id="test-session"):
        """Helper: directly write a token with the given op_type +
        session_id, bypassing write_token's sparse-context guard.

        SEC-S2 fixtures need to test op_type-symmetric retirement across
        merge / close / branch-delete / force-push, but write_token's
        sparse-context guard accepts any one of {pr_number, branch,
        operation_type}. Direct write ensures the test fixture matches
        exactly the op_type under test without relying on write_token's
        classification path.
        """
        from shared.merge_guard_common import MAX_USES

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {"operation_type": op_type},
            "session_id": session_id,
            "max_uses": MAX_USES,
            "uses_remaining": MAX_USES,
        }
        token_path = tmp_path / f"merge-authorized-{op_type}-token"
        token_path.write_text(json.dumps(token_data))
        return token_path

    def test_sec_s2_close_op_retirement_positive(self, tmp_path):
        """SEC-S2: gh pr close success retires the close-typed token.

        Block 3 substring "Closed pull request" (per
        LAYER1_SUCCESS_STDOUT_PATTERNS["close"]) gates retirement.

        # counter-test: change LAYER1_SUCCESS_STDOUT_PATTERNS["close"] to
        #               "wrong substring" in merge_guard_common.py → close
        #               command produces stdout without the new (wrong)
        #               substring; Block 3 rejects; token NOT retired.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import main

        token = self._write_session_token(tmp_path, "close", session_id="s-close")
        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr close 42"},
            "tool_response": {
                "stdout": "✓ Closed pull request #42",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("merge_guard_post.get_session_id", return_value="s-close"), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        assert not token.exists(), "SEC-S2: close-typed token not retired"
        consumed = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(consumed) == 1, "SEC-S2: expected exactly one .consumed"

    def test_sec_s2_close_op_retirement_pair_revert(self, tmp_path):
        """SEC-S2 pair-revert sentinel (close op_type).

        Phase 1: gh pr close success → close-typed token retires.
        Phase 2: gh issue close (NOT classified as close op_type by
        detect_command_operation_type — the classifier targets `gh pr
        close` specifically) → close-typed token NOT retired.

        # counter-test: revert Block 1 op_type filter in merge_guard_post
        #               (`if op_type not in LAYER1_SUCCESS_STDOUT_PATTERNS`)
        #               → phase 2 falls through and retires the token
        #               on the misclassified gh-issue-close command.
        # expected RED cardinality: {1} (phase 2 fails)
        """
        from merge_guard_post import main

        # Phase 1 — positive: gh pr close retires
        self._write_session_token(tmp_path, "close", session_id="s-pair-close")
        positive_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr close 42"},
            "tool_response": {
                "stdout": "✓ Closed pull request #42",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("merge_guard_post.get_session_id", return_value="s-pair-close"), \
             patch("sys.stdin", io.StringIO(positive_envelope)):
            with pytest.raises(SystemExit):
                main()
        phase1_consumed = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(phase1_consumed) == 1, (
            "Pair-revert phase 1 (positive): close command did NOT retire"
        )

        # Phase 2 — negative: gh issue close does NOT retire
        token2 = self._write_session_token(
            tmp_path, "close", session_id="s-pair-close"
        )
        # Rename to avoid collision with the now-.consumed first token
        token2_new = tmp_path / "merge-authorized-close-token-phase2"
        token2.rename(token2_new)
        negative_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue close 42"},
            "tool_response": {
                "stdout": "✓ Closed issue #42",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("merge_guard_post.get_session_id", return_value="s-pair-close"), \
             patch("sys.stdin", io.StringIO(negative_envelope)):
            with pytest.raises(SystemExit):
                main()
        assert token2_new.exists(), (
            "Pair-revert phase 2 (negative): gh issue close erroneously "
            "retired close-typed token (Block 1 op_type filter regressed)"
        )

    def test_sec_s2_branch_delete_op_retirement_positive(self, tmp_path):
        """SEC-S2: git branch -D success retires branch-delete-typed token.

        Block 3 substring "Deleted branch" gates retirement.

        # counter-test: change LAYER1_SUCCESS_STDOUT_PATTERNS["branch-delete"]
        #               to a wrong-substring → token NOT retired.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import main

        token = self._write_session_token(
            tmp_path, "branch-delete", session_id="s-bd"
        )
        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "git branch -D feat/x"},
            "tool_response": {
                "stdout": "Deleted branch feat/x (was a1b2c3d).",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("merge_guard_post.get_session_id", return_value="s-bd"), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        assert not token.exists(), "SEC-S2: branch-delete token not retired"
        consumed = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(consumed) == 1

    def test_sec_s2_branch_delete_op_retirement_pair_revert(self, tmp_path):
        """SEC-S2 pair-revert sentinel (branch-delete op_type).

        Phase 1: git branch -D feat/x → retires.
        Phase 2: git branch -d feat/x (lowercase -d is safe-delete; NOT
        classified as branch-delete by detect_command_operation_type) →
        does NOT retire.

        # counter-test: relax classifier to misclassify `git branch -d` as
        #               branch-delete → phase 2 retires (false-positive
        #               retirement on safe-delete).
        # expected RED cardinality: {1} (phase 2 fails)
        """
        from merge_guard_post import main

        # Phase 1 — positive
        self._write_session_token(
            tmp_path, "branch-delete", session_id="s-pair-bd"
        )
        positive_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "git branch -D feat/x"},
            "tool_response": {
                "stdout": "Deleted branch feat/x (was a1b2c3d).",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("merge_guard_post.get_session_id", return_value="s-pair-bd"), \
             patch("sys.stdin", io.StringIO(positive_envelope)):
            with pytest.raises(SystemExit):
                main()
        assert len(list(tmp_path.glob("*.consumed"))) == 1, (
            "Pair-revert phase 1: git branch -D did NOT retire"
        )

        # Phase 2 — negative: lowercase -d is safe-delete
        token2 = self._write_session_token(
            tmp_path, "branch-delete", session_id="s-pair-bd"
        )
        token2_new = tmp_path / "merge-authorized-bd-phase2"
        token2.rename(token2_new)
        negative_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "git branch -d feat/x"},
            "tool_response": {
                "stdout": "Deleted branch feat/x (was a1b2c3d).",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("merge_guard_post.get_session_id", return_value="s-pair-bd"), \
             patch("sys.stdin", io.StringIO(negative_envelope)):
            with pytest.raises(SystemExit):
                main()
        assert token2_new.exists(), (
            "Pair-revert phase 2: safe-delete `git branch -d` erroneously "
            "retired branch-delete-typed token (classifier regressed)"
        )

    def test_sec_s2_force_push_op_retirement_positive(self, tmp_path):
        """SEC-S2: git push --force success retires force-push-typed token
        via 2-block predicate (Block 3 skipped because table value is None).

        Force-push uses Block 3 = None — git push --force emits primarily
        to stderr, not stdout, so substring-matching stdout is structurally
        fragile. Block 2's platform-success implication carries the
        retirement decision for force-push.

        # counter-test: change LAYER1_SUCCESS_STDOUT_PATTERNS["force-push"]
        #               from None to a fixed string (e.g., "Everything
        #               up-to-date") that does NOT appear in the empty
        #               stdout → Block 3 rejects; token NOT retired.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import main

        token = self._write_session_token(
            tmp_path, "force-push", session_id="s-fp"
        )
        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "git push --force origin feat/x"},
            "tool_response": {
                # Empty stdout (force-push output goes to stderr).
                "stdout": "",
                "stderr": "+ abc1234...def5678 feat/x -> feat/x (forced update)",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("merge_guard_post.get_session_id", return_value="s-fp"), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        assert not token.exists(), (
            "SEC-S2: force-push token not retired via 2-block predicate"
        )
        consumed = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(consumed) == 1

    def test_sec_s2_force_push_op_retirement_pair_revert(self, tmp_path):
        """SEC-S2 pair-revert sentinel (force-push op_type).

        Phase 1: git push --force → retires.
        Phase 2: git push --force-with-lease (safe variant; classifier
        explicitly excludes via negative-lookahead) → does NOT retire.

        # counter-test: relax classifier negative-lookahead to misclassify
        #               --force-with-lease as force-push → phase 2 retires
        #               (false-positive retirement on safe variant).
        # expected RED cardinality: {1} (phase 2 fails)
        """
        from merge_guard_post import main

        # Phase 1 — positive
        self._write_session_token(
            tmp_path, "force-push", session_id="s-pair-fp"
        )
        positive_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "git push --force origin feat/x"},
            "tool_response": {
                "stdout": "",
                "stderr": "+ abc...def feat/x -> feat/x (forced update)",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("merge_guard_post.get_session_id", return_value="s-pair-fp"), \
             patch("sys.stdin", io.StringIO(positive_envelope)):
            with pytest.raises(SystemExit):
                main()
        assert len(list(tmp_path.glob("*.consumed"))) == 1, (
            "Pair-revert phase 1: git push --force did NOT retire"
        )

        # Phase 2 — negative: --force-with-lease is safe variant
        token2 = self._write_session_token(
            tmp_path, "force-push", session_id="s-pair-fp"
        )
        token2_new = tmp_path / "merge-authorized-fp-phase2"
        token2.rename(token2_new)
        negative_envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "git push --force-with-lease origin feat/x"},
            "tool_response": {
                "stdout": "",
                "stderr": "+ abc...def feat/x -> feat/x (forced update)",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("merge_guard_post.get_session_id", return_value="s-pair-fp"), \
             patch("sys.stdin", io.StringIO(negative_envelope)):
            with pytest.raises(SystemExit):
                main()
        assert token2_new.exists(), (
            "Pair-revert phase 2: --force-with-lease erroneously retired "
            "force-push token (classifier negative-lookahead regressed)"
        )

    def test_sec_s2_force_push_block2_structural_defense(self, tmp_path):
        """SEC-S2 force-push Block 2 fail-case (OQ-TE-3).

        Force-push has Block 3 = None — the 3-block predicate degrades to
        2 blocks (Block 1 op_type match + Block 2 platform success). This
        test pins Block 2 (interrupted=True rejection) as the structural
        defense for force-push: with Block 3 absent, Block 2 carries the
        success-implication load alone.

        # counter-test: remove the `interrupted is True` check in Block 2
        #               of the Bash branch → interrupted force-push retires
        #               the token despite user cancellation. Force-push is
        #               more exposed to this regression than merge/close/
        #               branch-delete because Block 3 cannot substring-
        #               reject it.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import main

        token = self._write_session_token(
            tmp_path, "force-push", session_id="s-fp-int"
        )
        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "git push --force origin feat/x"},
            "tool_response": {
                "stdout": "",
                "stderr": "",
                "interrupted": True,  # User cancellation
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("merge_guard_post.get_session_id", return_value="s-fp-int"), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        assert token.exists(), (
            "SEC-S2 force-push Block 2 violated: interrupted force-push "
            "retired the token (Block 2 was the only structural defense "
            "since Block 3 is None for force-push)"
        )
        consumed = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(consumed) == 0

    def test_sec_s2_lookup_table_ssot_pin(self):
        """SEC-S2 lookup-table SSOT pin: atomic-coupling enforcement.

        Pins the exact contents of LAYER1_SUCCESS_STDOUT_PATTERNS. Any
        mutation to the table (add op_type, remove op_type, change
        substring) MUST update this test in the same commit per CLAUDE.md
        "Coupling-via-substring-count: atomic-commit rule" generalized to
        dict-key cardinality.

        # counter-test: add a new key (e.g., "tag-delete": "Deleted tag")
        #               to LAYER1_SUCCESS_STDOUT_PATTERNS without updating
        #               this test → assertion fails.
        # counter-test: change LAYER1_SUCCESS_STDOUT_PATTERNS["force-push"]
        #               from None to a fixed string → assertion fails.
        # expected RED cardinality: {1}
        """
        from shared.merge_guard_common import LAYER1_SUCCESS_STDOUT_PATTERNS

        assert LAYER1_SUCCESS_STDOUT_PATTERNS == {
            "merge": "Merged pull request",
            "close": "Closed pull request",
            "branch-delete": "Deleted branch",
            "force-push": None,
            "push-to-main": None,
        }, (
            "SEC-S2 lookup-table SSOT drift: "
            "LAYER1_SUCCESS_STDOUT_PATTERNS mutated without atomic update "
            "to this pin"
        )


# =============================================================================
# #933 — merge_guard auth-token + dangerous-pattern defect fixes.
#
# Adversarial TEST CONTRACT (the architecture doc's §8) for the four fix
# surfaces:
#   D1-writer   — extract_context branch regex is flag-agnostic (the captured
#                 group is the branch NAME, never the delete FLAG).
#   D1-matcher  — _token_matches_command normalizes surrounding quotes at
#                 compare time, without widening the regex (no false-negative).
#   D2          — a 7th strip carrier exempts the non-executing quoted argument
#                 of a gh issue/pr CREATION verb; the carve-out and the
#                 compound pairing keep every real destructive op caught.
#   D3          — the post-hook keyword ladder recognizes a direct push to a
#                 default branch (main/master) as force-push.
#
# CARDINAL INVARIANT: never weaken detection of a real destructive op — over-
# block (false-positive) is acceptable; under-block (false-negative) is a
# security hole. Every fix carries a NEGATIVE that fails if the fix is mutated
# toward the forbidden behavior it guards. The mutation that proves each
# security negative non-vacuous is named per-test in a `# non-vacuity:` line.
#
# Dangerous literals live INSIDE these test-file string literals (never on a
# Bash command line), so this source file never trips merge_guard_pre.
# =============================================================================


# (removed class TestD1WriterBranchExtraction — superseded by the command-anchored
#  bidirectional suite in test_merge_guard_auth_symmetry.py;
#  it exercised the dropped prose classifier extract_context.)


class TestTargetAwareRetirement:
    """#1097 target-aware Layer-1 token retirement — the bidirectional cert.

    Pre-fix, `_retire_token_for_command` matched on op-type + session only, so a
    SUCCESSFUL unrelated same-op command (`gh pr close 43`) retired the operator's
    approved token for a DIFFERENT target (`close/42`) — an OVER-BLOCK: the
    faithful re-execution the operator approved then re-prompted. The fix computes
    `cmd_target = _target_value(extract_command_context(command))` once and skips
    any token whose own non-None target differs (`token_target is not None and
    cmd_target != token_target -> continue`). DEFENSIVE, not strict: a target-LESS
    token (degenerate/legacy shape) keeps the pre-fix op+session fallback, so
    nothing becomes un-retirable and no under-block opens.

    Priority per the governing principle: the over-block-cured direction
    (survival rows) is PRIMARY/inviolable; the still-retires direction is the
    secondary no-new-under-block sweep. ATTRIBUTION NOTE for main()-level rows:
    these tests call `_retire_token_for_command` DIRECTLY, below Block 3's stdout
    gate — an API-merge retiring command at main() level is Block-3-SKIPPED
    (its JSON success output does not contain LAYER1_SUCCESS_STDOUT_PATTERNS
    ["merge"]) and never reaches this predicate; the target predicate is
    load-bearing for CLI merge/close retiring commands, which DO emit the
    matching stdout. The live-probe covers the main()-level attribution split."""

    def _live(self, tmp_path):
        return [t for t in tmp_path.glob("merge-authorized-*")
                if not t.name.endswith(".consumed") and ".use-" not in t.name]

    # --- PRIMARY (inviolable): unrelated same-op does NOT retire a targeted token ---

    @pytest.mark.parametrize(
        "token_ctx,unrelated_cmd,op",
        [
            ({"operation_type": "close", "pr_number": "42",
              "bound_flags": ["--delete-branch"]}, "gh pr close 43", "close"),
            ({"operation_type": "merge", "pr_number": "42",
              "bound_flags": []}, "gh pr merge 43", "merge"),
        ],
    )
    def test_unrelated_same_op_success_does_not_retire_targeted_token(
        self, tmp_path, token_ctx, unrelated_cmd, op
    ):
        """#1097 CURED (the over-block direction): a successful unrelated same-op
        command must NOT burn the operator's approved token for a different
        target. Pre-fix this retired (op+session match); post-fix the non-None
        token target discriminates."""
        from merge_guard_post import write_token, _retire_token_for_command

        write_token(token_ctx, token_dir=tmp_path)
        retired = _retire_token_for_command(unrelated_cmd, op, token_dir=tmp_path)
        assert retired is False, (
            f"OVER-BLOCK regressed: unrelated {unrelated_cmd!r} retired the "
            f"{token_ctx['operation_type']}/{token_ctx['pr_number']} token"
        )
        assert len(self._live(tmp_path)) == 1, "the protected token must survive"

    def test_no_target_command_does_not_retire_targeted_token(self, tmp_path):
        """Safe-direction edge: a retiring command with NO extractable target
        (bare `gh pr merge`) retires nothing against a targeted token —
        cmd_target None != token_target '42'."""
        from merge_guard_post import write_token, _retire_token_for_command

        write_token({"operation_type": "merge", "pr_number": "42",
                     "bound_flags": []}, token_dir=tmp_path)
        retired = _retire_token_for_command("gh pr merge", "merge", token_dir=tmp_path)
        assert retired is False
        assert len(self._live(tmp_path)) == 1

    def test_api_merge_target_composes_with_retirement(self, tmp_path):
        """#1096 composition at the predicate level: the API-merge form's
        pr_number (path-resident, `pulls/<N>/merge`) flows through the SAME
        `_target_value` the retirement predicate uses — an unrelated API merge
        (43) does not retire the 42 token; the same-target API form does.
        (At main() level an API-merge retiring command is Block-3-skipped
        entirely — see the class docstring; this row certifies the predicate's
        target arithmetic for the API spelling, the belt-and-suspenders layer.)"""
        from merge_guard_post import write_token, _retire_token_for_command

        write_token({"operation_type": "merge", "pr_number": "42",
                     "bound_flags": []}, token_dir=tmp_path)
        unrelated = _retire_token_for_command(
            "gh api -X PUT /repos/o/r/pulls/43/merge", "merge", token_dir=tmp_path)
        assert unrelated is False
        assert len(self._live(tmp_path)) == 1
        same = _retire_token_for_command(
            "gh api -X PUT /repos/o/r/pulls/42/merge", "merge", token_dir=tmp_path)
        assert same is True
        assert len(self._live(tmp_path)) == 0

    # --- SECONDARY (no-new-under-block): genuine self-consume STILL retires ---

    def test_self_consume_still_retires_own_token(self, tmp_path):
        """A successful run of the SAME approved operation retires its own token
        (target matches) — the one-approval-one-operation discipline is intact."""
        from merge_guard_post import write_token, _retire_token_for_command

        write_token({"operation_type": "close", "pr_number": "42",
                     "bound_flags": ["--delete-branch"]}, token_dir=tmp_path)
        retired = _retire_token_for_command(
            "gh pr close 42 --delete-branch", "close", token_dir=tmp_path)
        assert retired is True, "NEW UNDER-BLOCK: self-consume stopped retiring"
        assert len(self._live(tmp_path)) == 0
        second = _retire_token_for_command(
            "gh pr close 42 --delete-branch", "close", token_dir=tmp_path)
        assert second is False, "second retire must find no live candidate"

    def test_target_less_token_keeps_op_session_fallback(self, tmp_path):
        """The DEFENSIVE half: a target-LESS token (degenerate/legacy shape —
        no production mint writes one, see the target-completeness test below)
        keeps the pre-fix op+session retirement, so nothing becomes un-retirable.
        The ~5 pre-existing target-less retirement tests in
        TestTokenLifecycleExtensions pin the same fallback end-to-end."""
        from merge_guard_post import write_token, _retire_token_for_command

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        retired = _retire_token_for_command("gh pr merge 99", "merge", token_dir=tmp_path)
        assert retired is True, (
            "defensive fallback broken: target-less token no longer retired by "
            "an op+session match — the predicate was implemented STRICT (bug)"
        )

    # --- target-completeness (the by-construction claim, verified) ---

    def test_every_production_mint_writes_a_targeted_token(self, tmp_path):
        """The defensive fallback's coarse-retirement residual is provably EMPTY
        in production: every op-class's canonical approval mints a context whose
        `_target_value` is non-None. Source audit (the completeness half):
        `write_token` has exactly ONE production call site (merge_guard_post
        main's mint path), fed only by `_mint_context_from_bundle`, whose
        `_collect_pairs` admits a pair ONLY when `op_type is not None and
        target is not None` — so no mint path can write a target-less token.
        This sweep is the per-op-class empirical corroboration."""
        from merge_guard_post import _mint_context_from_bundle, _target_value

        approvals = [
            "gh pr merge 42",
            "gh pr close 42 --delete-branch",
            "git push --force origin main",
            "git push origin main",
            "git branch -D victim",
            "git push origin :feature",
            "git push --prune origin",
            "gh api -X DELETE repos/o/r/branches/main/protection",
            "gh api -X PUT /repos/o/r/pulls/42/merge",
        ]
        for cmd in approvals:
            question = {
                "question": "Proceed?",
                "options": [{"label": "Yes, do it",
                             "description": f"Run `{cmd}` now"}],
                "multiSelect": False,
            }
            ctx, refusal = _mint_context_from_bundle(
                [question], {"Proceed?": "Yes, do it"})
            assert ctx is not None, f"canonical approval failed to mint: {cmd!r} ({refusal})"
            assert _target_value(ctx) is not None, (
                f"TARGET-LESS PRODUCTION MINT for {cmd!r} — the defensive "
                f"fallback's coarse retirement is REACHABLE in production "
                f"(separate pre-existing over-block; STOP and flag)"
            )

    # --- non-vacuity: the survival rows are coupled to the target predicate ---

    def test_target_predicate_non_vacuous_under_target_value_neuter(
        self, tmp_path, monkeypatch
    ):
        """Two-direction counter-mutation: with `_target_value` neutered to
        always-None (the pre-fix surface — no target ever discriminates, every
        token falls to the op+session fallback), the unrelated same-op command
        RETIRES the operator's 42 token again — proving the survival rows above
        are coupled to the #1097 target predicate, not vacuously green.
        In-memory (monkeypatch) by design — no git mutation in the shared
        worktree."""
        import merge_guard_post as mgp

        # direction 1 — fix present: the unrelated command does not retire.
        # FLAGLESS token + bare command: with the #1100 flag axis now also live,
        # the token and command flag-sets must be EQUAL here (both empty) so this
        # test isolates the TARGET predicate — otherwise a flag mismatch would be
        # a second, independent blocker and the target-neuter alone could not
        # restore coarse retirement (the #1100/#1097 interaction). Survival in
        # direction 1 is therefore attributable solely to the target mismatch.
        mgp.write_token({"operation_type": "close", "pr_number": "42",
                         "bound_flags": []}, token_dir=tmp_path)
        assert mgp._retire_token_for_command(
            "gh pr close 43", "close", token_dir=tmp_path) is False
        assert len(self._live(tmp_path)) == 1
        # direction 2 — pre-fix surface restored: the coarse retirement returns.
        # Flags stay equal ([] == []) so neutering _target_value is the ONLY
        # change, and it flips survive -> retire.
        monkeypatch.setattr(mgp, "_target_value", lambda ctx: None)
        assert mgp._retire_token_for_command(
            "gh pr close 43", "close", token_dir=tmp_path) is True, (
            "target-value neuter did not restore the pre-fix coarse retirement "
            "— the survival assertions above would be vacuous"
        )
        assert len(self._live(tmp_path)) == 0


class TestFlagAwareRetirement:
    """#1100 flag-aware Layer-1 token retirement — the bidirectional cert.

    #1097 raised the retirement match key to op+target; #1100 raises it once
    more to op+target+bound_flags so a successful BARE same-op/same-target
    command no longer retires an operator's approved ESCALATED (flag-carrying)
    token — the bounded-friction OVER-BLOCK. `_retire_token_for_command`
    reuses the mint-stored `bound_flags` and the SAME `extract_privileged_flags`
    SSOT the read arm (merge_guard_pre) set-compares, adding an unconditional
    `set(ctx.bound_flags) != cmd_flags` skip AFTER the existing target check.

    Governing principle (task-level SACROSANCT): retirement is a PostToolUse
    OBSERVER — it can only fail-to-retire, never over-block a faithful click.
    The flag axis is a pure ADDITIONAL conjunctive skip, so:
      - PRIMARY (inviolable): an escalated token survives a bare same-target
        command (the #1100 cure) — RED on pre-#1100 main, GREEN after.
      - SECONDARY (no-new-under-block): a genuine self-consume (matching
        flags, INCLUDING the flagless case) STILL retires.

    Tokens are minted THROUGH `extract_command_context` (the production SSOT)
    so their stored `bound_flags` are scanner-derived, not hand-picked
    literals — this is what lets the row-4 neuter collapse BOTH the command-
    side and token-side flag sets symmetrically. In-memory only (write_token /
    _retire_token_for_command with token_dir=tmp_path); NO git mutation in the
    shared worktree (#1097 precedent)."""

    def _live(self, tmp_path):
        return [t for t in tmp_path.glob("merge-authorized-*")
                if not t.name.endswith(".consumed") and ".use-" not in t.name]

    @staticmethod
    def _mint_from(cmd, tmp_path):
        """Mint a token whose context (incl. bound_flags) is derived by the
        production SSOT extract_command_context — faithful to what a real
        approval of `cmd` would store, so the flag set is scanner-derived
        rather than a hand-picked literal."""
        from merge_guard_post import write_token, extract_command_context

        path = write_token(extract_command_context(cmd), token_dir=tmp_path)
        assert path is not None, f"mint failed for {cmd!r}"
        return path

    # --- SECONDARY (no-new-under-block): matching-flag self-consume STILL retires ---

    @pytest.mark.parametrize(
        "mint_cmd,retire_cmd,op",
        [
            ("gh pr merge 42 --admin", "gh pr merge 42 --admin", "merge"),
            ("gh pr close 42 --delete-branch",
             "gh pr close 42 --delete-branch", "close"),
            ("git push --no-verify origin main --force",
             "git push --no-verify origin main --force", "force-push"),
            ("git push --force-with-lease origin main",
             "git push --force-with-lease origin main", "push-to-main"),
            # FLAGLESS self-consume — guards an over-strict "token must have
            # flags" implementation: a bare-approved bare-executed op MUST
            # still retire (empty set == empty set).
            ("gh pr close 5", "gh pr close 5", "close"),
        ],
        ids=["merge_admin", "close_delete_branch", "force_push_no_verify",
             "push_to_main_lease", "flagless_close"],
    )
    def test_matching_flag_self_consume_still_retires(
        self, tmp_path, mint_cmd, retire_cmd, op
    ):
        """No-new-under-block: an execution whose flag-set EQUALS the approved
        token's (incl. the empty set) retires its own token — the
        one-approval-one-operation discipline holds across every op-class."""
        from merge_guard_post import _retire_token_for_command

        self._mint_from(mint_cmd, tmp_path)
        retired = _retire_token_for_command(retire_cmd, op, token_dir=tmp_path)
        assert retired is True, (
            f"NEW UNDER-BLOCK: self-consume stopped retiring for {op} ({retire_cmd!r})"
        )
        assert len(self._live(tmp_path)) == 0

    # --- PRIMARY (inviolable): flag mismatch at same op+target SURVIVES (#1100 cure) ---

    @pytest.mark.parametrize(
        "mint_cmd,retire_cmd,op",
        [
            # Escalated token, bare execution — the operator's approved
            # --delete-branch token must NOT burn on a bare `gh pr close 5`.
            ("gh pr close 5 --delete-branch", "gh pr close 5", "close"),
            # Reciprocal: bare token, escalated execution — the bare token
            # is a distinct identity from the --delete-branch variant.
            ("gh pr close 5", "gh pr close 5 --delete-branch", "close"),
            # merge/--admin mirror, both directions.
            ("gh pr merge 42 --admin", "gh pr merge 42", "merge"),
            ("gh pr merge 42", "gh pr merge 42 --admin", "merge"),
        ],
        ids=["escalated_survives_bare_close", "bare_survives_escalated_close",
             "escalated_survives_bare_merge", "bare_survives_escalated_merge"],
    )
    def test_flag_mismatch_same_target_survives(
        self, tmp_path, mint_cmd, retire_cmd, op
    ):
        """#1100 CURED (RED on pre-#1100 main): same op + same target but a
        DIFFERENT flag-set must NOT retire — the bare and escalated forms are
        distinct token identities. Pre-#1100 retirement ignored flags, so a
        bare command over-retired the escalated token (the bounded friction)."""
        from merge_guard_post import _retire_token_for_command

        self._mint_from(mint_cmd, tmp_path)
        retired = _retire_token_for_command(retire_cmd, op, token_dir=tmp_path)
        assert retired is False, (
            f"#1100 OVER-BLOCK: {retire_cmd!r} retired the differently-flagged "
            f"{mint_cmd!r} token"
        )
        assert len(self._live(tmp_path)) == 1, "the escalated/bare token must survive"

    # --- flag-normalization symmetry: equal NORMALIZED sets retire (reuse of
    #     extract_privileged_flags, not a literal-key compare) ---

    @pytest.mark.parametrize(
        "mint_cmd,retire_cmd,op",
        [
            # -d is the short alias of --delete-branch: same canonical set.
            ("gh pr close 42 --delete-branch", "gh pr close 42 -d", "close"),
            # --repo value across =-joined / space / attached-short spellings.
            ("gh pr close 7 --repo=o/r", "gh pr close 7 --repo o/r", "close"),
            ("gh pr close 7 --repo=o/r", "gh pr close 7 -Ro/r", "close"),
            # Reordered multi-flag: sets are order-insensitive.
            ("gh pr merge 9 --repo=o/r --admin",
             "gh pr merge 9 --admin --repo=o/r", "merge"),
            # Duplicate flag collapses to the same singleton set.
            ("gh pr merge 9 --admin", "gh pr merge 9 --admin --admin", "merge"),
            # A benign non-privileged flag (--json) is not part of the bound set.
            ("gh pr merge 9 --admin", "gh pr merge 9 --admin --json number", "merge"),
            # Value-bearing round-trip: identical --repo value retires.
            ("gh pr close 7 --repo=o/r", "gh pr close 7 --repo=o/r", "close"),
        ],
        ids=["delete_branch_long_vs_short", "repo_eq_vs_space",
             "repo_eq_vs_attached_short", "multi_flag_reordered", "duplicate_flag",
             "benign_json_alongside", "repo_value_round_trip"],
    )
    def test_normalized_flag_equivalence_retires(
        self, tmp_path, mint_cmd, retire_cmd, op
    ):
        """The approved and executed forms differ TEXTUALLY but normalize to the
        SAME privileged-flag set, so the self-consume still retires. A literal
        string compare (not reuse of extract_privileged_flags) would MISMATCH
        `-d` vs `--delete-branch` and wrongly leave the token live."""
        from merge_guard_post import _retire_token_for_command

        self._mint_from(mint_cmd, tmp_path)
        retired = _retire_token_for_command(retire_cmd, op, token_dir=tmp_path)
        assert retired is True, (
            f"normalization broke: {retire_cmd!r} did not retire the equivalent "
            f"{mint_cmd!r} token — flags compared as literals, not normalized sets"
        )
        assert len(self._live(tmp_path)) == 0

    def test_repo_value_mismatch_survives(self, tmp_path):
        """The --repo VALUE is part of the bound-flag identity (a value-carrying
        flag): approve `--repo=o/r`, execute `--repo=x/y` at the same target ->
        distinct sets -> survives. RED on pre-#1100 main (flags ignored)."""
        from merge_guard_post import _retire_token_for_command

        self._mint_from("gh pr close 7 --repo=o/r", tmp_path)
        retired = _retire_token_for_command(
            "gh pr close 7 --repo=x/y", "close", token_dir=tmp_path)
        assert retired is False, "cross-repo value redirect retired the o/r token"
        assert len(self._live(tmp_path)) == 1

    # --- non-vacuity: the survival rows are coupled to the flag predicate ---

    def test_flag_value_is_load_bearing_black_box(self, tmp_path):
        """SEAM-INDEPENDENT primary non-vacuity: holding op, target, and session
        fixed, the retire/survive outcome flips on the flag-set ALONE — matching
        flags retire, mismatched flags survive. Robust regardless of the hook's
        internal extraction seam."""
        from merge_guard_post import _retire_token_for_command

        # matching flags -> retire
        self._mint_from("gh pr close 5 --delete-branch", tmp_path)
        assert _retire_token_for_command(
            "gh pr close 5 --delete-branch", "close", token_dir=tmp_path) is True
        assert len(self._live(tmp_path)) == 0
        # same op+target, mismatched flags -> survive (RED on pre-#1100 main)
        self._mint_from("gh pr close 5 --delete-branch", tmp_path)
        assert _retire_token_for_command(
            "gh pr close 5", "close", token_dir=tmp_path) is False
        assert len(self._live(tmp_path)) == 1

    def test_flag_predicate_non_vacuous_under_extract_neuter(
        self, tmp_path, monkeypatch
    ):
        """Counter-mutation mirroring the #1097 target-neuter: with the shared
        `extract_privileged_flags` SSOT neutered to always-[], BOTH the
        command's cmd_flags AND a freshly-minted token's stored bound_flags
        collapse to the empty set, so the escalated-vs-bare distinction
        disappears and the coarse op+target retirement returns — proving the
        survival rows are coupled to the flag predicate, not vacuously green.
        In-memory (monkeypatch) by design — no git mutation in the worktree."""
        from merge_guard_post import _retire_token_for_command

        # direction 1 — predicate present: escalated token survives bare command
        self._mint_from("gh pr close 5 --delete-branch", tmp_path)
        assert _retire_token_for_command(
            "gh pr close 5", "close", token_dir=tmp_path) is False
        assert len(self._live(tmp_path)) == 1
        # direction 2 — neuter the SSOT both arms derive flags from; re-mint so
        # the token's stored bound_flags also collapse to [] (symmetric neuter)
        monkeypatch.setattr(
            "shared.merge_guard_common.extract_privileged_flags",
            lambda *a, **k: [])
        self._mint_from("gh pr close 5 --delete-branch", tmp_path)
        assert _retire_token_for_command(
            "gh pr close 5", "close", token_dir=tmp_path) is True, (
            "flag-extraction neuter did not restore coarse retirement — the "
            "survival assertions above would be vacuous"
        )
        assert len(self._live(tmp_path)) == 0

    # --- both-modes: identical flag-axis behavior across session states ---

    @pytest.mark.parametrize(
        "session_id", ["", "sess-A"],
        ids=["empty_session", "populated_matching_session"],
    )
    def test_flag_axis_identical_across_session_states(
        self, tmp_path, monkeypatch, session_id
    ):
        """Dual-mode contract: the flag axis behaves identically whether
        retirement runs with no PACT session (get_session_id()=='' -> the
        session gate is skipped, graceful degradation) or a populated session
        matching the token's own session_id. (This surface's session axis is
        empty-vs-populated-matching — NOT leadSessionId topology, which this
        hook does not key on.) Both the matching-flag self-consume (retire) and
        the flag-mismatch friction (survive) are asserted under each state."""
        import merge_guard_post as mgp
        from merge_guard_post import _retire_token_for_command

        monkeypatch.setattr(mgp, "get_session_id", lambda: session_id)
        # matching flags -> retire, in both session states
        self._mint_from("gh pr close 5 --delete-branch", tmp_path)
        assert _retire_token_for_command(
            "gh pr close 5 --delete-branch", "close", token_dir=tmp_path) is True
        assert len(self._live(tmp_path)) == 0
        # flag mismatch -> survive, in both session states (RED on pre-#1100 main)
        self._mint_from("gh pr close 5 --delete-branch", tmp_path)
        assert _retire_token_for_command(
            "gh pr close 5", "close", token_dir=tmp_path) is False
        assert len(self._live(tmp_path)) == 1


class TestMalformedTokenRetirementRobustness:
    """#1100 follow-up (Copilot PR review): a token whose stored `bound_flags`
    is malformed must never crash `_retire_token_for_command` — the retirement
    observer is fail-safe (never raises, never blocks the caller). Two malformed
    classes both made `set(token_flags)` raise: (1) a NON-list value (JSON null ->
    None, or int/float/bool/str) -> `set(None)` TypeError; (2) a LIST containing a
    non-string / unhashable element (`[{}]`, `[["x"]]`) -> `unhashable type`. The
    token-side read is hardened with `isinstance(token_flags, list) and
    all(isinstance(x, str) for x in token_flags)`; a malformed token is SKIPPED,
    and — critically — a malformed token must not poison the rest of the
    retirement loop, so a VALID token in the same directory still retires.

    Tokens are written to disk DIRECTLY (bypassing write_token) so a malformed
    `bound_flags` the production mint path would never emit can be injected, and
    so two tokens can coexist (write_token's Layer-5 cleanup retires priors).
    In-memory only; no git mutation in the shared worktree."""

    def _live(self, tmp_path):
        return [t for t in tmp_path.glob("merge-authorized-*")
                if not t.name.endswith(".consumed") and ".use-" not in t.name]

    @staticmethod
    def _write_token_json(tmp_path, context, name="merge-authorized-1000"):
        """Write a token file directly with an arbitrary context (so a malformed
        `bound_flags` can be injected). Mirrors write_token's on-disk shape."""
        now = time.time()
        data = {"created_at": now, "expires_at": now + 300,
                "context": context, "max_uses": 2, "uses_remaining": 2}
        path = tmp_path / name
        path.write_text(json.dumps(data))
        return path

    @pytest.mark.parametrize(
        "bad_flags",
        [
            # non-list shapes — closed by the `isinstance(token_flags, list)` clause
            None, 7, 1.5, True, "notalist",
            # list WITH a non-string / unhashable element — PASSES isinstance-list
            # but `set([{}])` / `set([["x"]])` raises `unhashable type`; closed by
            # the `all(isinstance(x, str) for x in token_flags)` element clause.
            # Without these rows that element clause would ship UNTESTED.
            [{}], [["x"]],
        ],
        ids=["none", "int", "float", "bool", "string",
             "list_with_dict", "list_with_list"],
    )
    def test_malformed_bound_flags_does_not_crash_and_is_skipped(
        self, tmp_path, bad_flags
    ):
        """A token whose bound_flags is malformed — either NOT a list (None/int/
        float/bool/string) OR a list containing a non-string/unhashable element
        (`[{}]`, `[["x"]]`) — must NOT crash the retirement observer and must NOT
        be retired. Driven with a NON-empty command flag-set (`--delete-branch`)
        so the malformed token cannot match whether the guard SKIPS it or coerces
        bound_flags to [] — robust to the guard's exact shape. Without the guard
        the non-list crashers AND the unhashable-element lists raise `set(...)`
        TypeError (closed by the isinstance-list clause and the
        all(isinstance(x, str)) element clause respectively); the string case
        never crashes (char-set) but is still skipped."""
        from merge_guard_post import _retire_token_for_command

        self._write_token_json(
            tmp_path,
            {"operation_type": "close", "pr_number": "5", "bound_flags": bad_flags},
        )
        try:
            retired = _retire_token_for_command(
                "gh pr close 5 --delete-branch", "close", token_dir=tmp_path)
        except Exception as exc:  # noqa: BLE001 — the observer contract is never-raise
            pytest.fail(
                f"malformed bound_flags ({bad_flags!r}) crashed the retirement "
                f"observer: {type(exc).__name__}: {exc}"
            )
        assert retired is False, "a malformed-flags token must not be retired"
        assert len(self._live(tmp_path)) == 1, (
            "the malformed token is skipped (survives), not consumed"
        )

    @pytest.mark.parametrize(
        "bad_flags", [None, [{}]], ids=["non_list", "list_with_unhashable"]
    )
    def test_malformed_token_does_not_poison_valid_retirement(
        self, tmp_path, monkeypatch, bad_flags
    ):
        """RESILIENCE (the core of the finding): a malformed token encountered
        BEFORE a valid matching token must not abort the loop — the valid token
        still retires. `_retire_token_for_command` returns on the FIRST match and
        glob order is filesystem-arbitrary, so we FORCE the malformed token first
        via a monkeypatched glob to make the hazard deterministic (otherwise the
        test could pass by luck of iteration order). Covers BOTH crash surfaces
        (non-list and unhashable-element list). RED before the guard: the
        malformed token raises and the valid token is never reached."""
        import merge_guard_post as mgp
        from merge_guard_post import _retire_token_for_command

        malformed = self._write_token_json(
            tmp_path,
            {"operation_type": "close", "pr_number": "5", "bound_flags": bad_flags},
            name="merge-authorized-1000",
        )
        valid = self._write_token_json(
            tmp_path,
            {"operation_type": "close", "pr_number": "5",
             "bound_flags": ["--delete-branch"]},
            name="merge-authorized-2000",
        )
        # Visit the malformed token FIRST (deterministic hazard ordering).
        monkeypatch.setattr(
            mgp.glob, "glob", lambda pattern: [str(malformed), str(valid)])
        try:
            retired = _retire_token_for_command(
                "gh pr close 5 --delete-branch", "close", token_dir=tmp_path)
        except Exception as exc:  # noqa: BLE001 — the observer contract is never-raise
            pytest.fail(
                f"a malformed token poisoned the retirement loop: "
                f"{type(exc).__name__}: {exc}"
            )
        assert retired is True, (
            "the valid token must still retire after the malformed one is skipped"
        )
        live = self._live(tmp_path)
        assert len(live) == 1 and live[0].name == "merge-authorized-1000", (
            "the malformed token is skipped (survives); the valid token is consumed"
        )


class TestD1MatcherQuoteNormalization:
    """D1-matcher (#933): _token_matches_command normalizes surrounding quotes
    on the captured branch name at compare time, so a token branch `feat/x`
    matches a command that quotes the branch argument — WITHOUT widening the
    regex (the capture stays `(\\S+)`; only the comparison normalizes), so no
    mismatched branch is newly accepted.
    """

    @staticmethod
    def _tok(branch):
        return {"context": {"branch": branch, "operation_type": "branch-delete"}}

    def test_matches_single_quoted_branch(self):
        from merge_guard_pre import _token_matches_command

        assert _token_matches_command(self._tok("feat/x"), "git branch -D 'feat/x'")

    def test_matches_double_quoted_branch(self):
        from merge_guard_pre import _token_matches_command

        assert _token_matches_command(self._tok("feat/x"), 'git branch -D "feat/x"')

    def test_matches_force_delete_quoted_branch(self):
        from merge_guard_pre import _token_matches_command

        assert _token_matches_command(
            self._tok("feat/x"), "git branch --force --delete 'feat/x'"
        )

    def test_matches_unquoted_branch_unchanged(self):
        """REGRESSION GUARD: the unquoted form still matches (quote-strip is a
        no-op on an unquoted token). Mirrors test_token_with_matching_branch."""
        from merge_guard_pre import _token_matches_command

        assert _token_matches_command(self._tok("feat/x"), "git branch -D feat/x")

    # ---- NEGATIVE / revert-proving ----

    def test_mismatched_quoted_branch_rejected(self):
        """SECURITY NEGATIVE: a DIFFERENT branch, quoted, is still rejected —
        proving the quote-strip normalized the value without widening which
        branches match.

        # non-vacuity: replace the buggy/un-widened behavior by mutating the
        #   matcher to compare AFTER stripping BOTH operands' quotes-and-more
        #   (or to substring-match) — any widening makes this pass-through.
        #   The committed guard against widening: this asserts a real
        #   non-match survives the quote-strip. A regex/compare that widened
        #   to accept any quoted token would FAIL this.
        """
        from merge_guard_pre import _token_matches_command

        assert not _token_matches_command(self._tok("feat/x"), "git branch -D 'other'")

    def test_substring_quoted_branch_rejected(self):
        """SECURITY NEGATIVE: a SUPERSTRING branch (`feat/x-extra`), quoted,
        is rejected — the quote-strip yields `feat/x-extra` which is != the
        token `feat/x` under exact comparison. Proves no substring widening.

        # non-vacuity: a matcher that did `branch in stripped` (substring)
        #   instead of `stripped == branch` would ACCEPT `feat/x-extra` for a
        #   `feat/x` token → this assertion FAILS. Pins exact-equality.
        """
        from merge_guard_pre import _token_matches_command

        assert not _token_matches_command(
            self._tok("feat/x"), "git branch -D 'feat/x-extra'"
        )


class TestD2GhCarrierStrip:
    """D2 (#933): the 7th strip carrier exempts the non-executing quoted
    argument of a gh issue/pr CREATION verb (`gh issue create|edit`,
    `gh pr create`) — so a dangerous-op literal named inside a `--title`/
    `--body`/`-t`/`-b` value no longer trips DANGEROUS_PATTERNS.

    Positive cases pin the false-positive FIX. The deviation pins (two-value
    strip) lock the AS-BUILT carrier-span behavior, which differs from the
    architecture doc's single-re.sub literal (the per-span inner-strip strips
    BOTH a --title and a --body on one command).
    """

    # ---- POSITIVE: benign carrier no longer over-blocks ----

    def test_issue_create_title_with_danger_literal_not_dangerous(self):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'gh issue create --title "repro: git branch -D feat/x" --body "ctx"'
        )

    def test_issue_edit_body_with_push_literal_not_dangerous(self):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'gh issue edit 5 --body "see git push --force origin main"'
        )

    def test_pr_create_title_with_danger_literal_not_dangerous(self):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'gh pr create --title "fix: git branch -D cleanup"'
        )

    def test_issue_create_single_quoted_title_not_dangerous(self):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            "gh issue create --title 'repro: git branch -D feat/x'"
        )

    # ---- DEVIATION PIN: two-value strip (the as-built carrier-span shape) ----

    def test_two_value_strip_both_aliases(self):
        """As-built deviation pin: `-t "..." -b "..."` (two carriers on one
        command) strips BOTH values. A single global re.sub would consume the
        verb prefix on the first match and leave the SECOND value un-stripped
        (a false-positive over-block); the carrier-span inner-strip fixes that.

        # non-vacuity: revert the 7th carrier to a single-re.sub-per-quote-
        #   style form (strip only the first --title/--body) → the second
        #   value's `git push --force` survives → is_dangerous returns True →
        #   this assertion FAILS.
        """
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'gh issue create -t "git branch -D a" -b "git push --force b"'
        )

    def test_two_value_strip_long_flags(self):
        """Two-value strip with the long --title/--body flags (general, not
        alias-specific)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'gh issue create --title "git branch -D a" --body "git push --force b"'
        )

    # ---- SECURITY NEGATIVES / revert-proving (the highest-value tests) ----

    def test_compound_real_op_after_carrier_still_dangerous(self):
        """SECURITY NEGATIVE: a real destructive op after `&&` survives the
        strip and stays caught. TWO independent layers protect the tail —
        the carrier span stops at the `&` separator (so the tail is OUTSIDE
        the span), AND the inner strip only removes QUOTED flag-VALUES (so an
        unquoted op is never stripped even if it sits inside the span). The
        fix is defense-in-depth: each layer blocks the over-strip alone.

        # non-vacuity: a single-layer mutation does NOT flip this (verified:
        #   broadening the span `[^&|;]*`→`.*` leaves the tail un-stripped
        #   because the inner strip touches only quoted flag-values; a greedy
        #   inner-strip leaves it un-stripped because the span still stops at
        #   `&&`). The faithful regression is the whole-span OVER-STRIP
        #   ("carrier over-strips and hides a real op", arch §10 risk row 1):
        #   broaden the span to `.*` AND blank the entire matched span →
        #   `git branch -D real-branch` is consumed → is_dangerous returns
        #   False → this assertion FAILS (verified RED). The two compound
        #   tests + the backgrounded-& test + the unquoted-token test all
        #   flip together under this over-strip. Expected RED cardinality: {4}.
        """
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --title "notes" && git branch -D real-branch'
        )

    def test_compound_real_op_after_carrier_single_destructive_not_compound(self):
        """Honest-mistake ≥2-narrowing: `gh issue create --title "notes" && git branch
        -D real-branch` has ONE destructive leg (the branch-delete; `gh issue create`
        is benign) → NOT compound. The SECURITY property is preserved at the
        is_dangerous layer: the executing `git branch -D` tail survives the carrier
        strip and is is_dangerous-gated (asserted by the sibling test just above) — so
        the single destructive op is still caught, it is simply not >=2-compound."""
        from merge_guard_pre import is_compound_destructive_command, is_dangerous_command

        cmd = 'gh issue create --title "notes" && git branch -D real-branch'
        assert is_compound_destructive_command(cmd) is False
        assert is_dangerous_command(cmd) is True  # the branch-delete tail is still gated

    def test_backgrounded_carrier_real_op_after_amp_still_dangerous(self):
        """SECURITY NEGATIVE (CODE-HANDOFF flagged: the single-`&` background
        case): a single `&` (backgrounding) also terminates the carrier span,
        so a real op after `gh issue create ... &` is OUTSIDE the span and
        stays caught. (`[^&|;]*` stops at the first `&`, logical or not.)

        # non-vacuity: the whole-span over-strip (broaden span to `.*` AND
        #   blank the matched span) consumes the backgrounded tail →
        #   is_dangerous returns False → this assertion FAILS (verified RED,
        #   part of the {4}-test cluster). A span-boundary-only broadening
        #   does NOT flip it (inner strip is quoted-value-scoped). Pins that
        #   backgrounding does not smuggle a real op past the scan.
        """
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --title "bg" & git branch -D real-branch'
        )

    def test_pr_close_delete_branch_still_dangerous_carveout(self):
        """SECURITY NEGATIVE (the carve-out): `gh pr close --delete-branch` is
        NEVER exempted — `close` is absent from the carrier verb alternation,
        so the command is not stripped and DANGEROUS_PATTERNS still fires.

        Two assertions, because the carve-out has two independent protective
        layers and the behavioral one alone is VACUOUS w.r.t. the carrier:

        1. Behavioral: the command stays dangerous. NOTE this is robust even
           if `close` were wrongly added to the carrier verbs — the deny
           trigger is the bare `--delete-branch` flag (DANGEROUS_PATTERNS
           `pr close (?=.*--delete-branch)`), NOT a quoted value, so the
           quoted-value-only inner strip can never neutralize it. So this
           line does NOT, by itself, prove the verb exclusion is load-bearing.
        2. Mechanism-level: on a `gh pr close` command that ALSO carries a
           quoted value, the value SURVIVES the strip (close is not a carrier).
           THIS is the assertion that isolates the verb exclusion.

        # non-vacuity: a removal-revert of the 7th carrier does NOT prove the
        #   carve-out (close was never a carrier verb → both lines stay GREEN
        #   == phantom-green). Prove it via the EXCLUSION-GUARD pattern:
        #   BROADEN the verb alternation to include `close`
        #   (`pr\\s+(?:create|close)`) in an isolated edit → the quoted value
        #   is stripped → assertion (2) FAILS (verified RED). Assertion (1)
        #   correctly stays green under that mutation (the `--delete-branch`
        #   deny trigger is not a quoted value) — which is exactly why (2) is
        #   the load-bearing pin, not (1). Restore after.
        """
        from merge_guard_pre import (
            _strip_non_executable_content,
            is_dangerous_command,
        )

        # (1) Behavioral — stays dangerous (robust, but vacuous w.r.t. carrier).
        assert is_dangerous_command("gh pr close 42 --delete-branch")

        # (2) Mechanism-level — the quoted value on a close command is NOT
        #     stripped, because `close` is excluded from the carrier verbs.
        close_cmd = 'gh pr close 42 --delete-branch --body "git branch -D x"'
        assert '"git branch -D x"' in _strip_non_executable_content(close_cmd)

    def test_command_substitution_in_title_preserved_dangerous(self):
        """SECURITY NEGATIVE (command-substitution guard): a `$(...)` inside a
        double-quoted --body EXECUTES, so it is preserved (not stripped) and
        the command stays dangerous.

        # non-vacuity: remove the `_has_command_substitution` guard in the
        #   double-quoted arm (always strip) → the `$(git branch -D x)`
        #   value is stripped → is_dangerous returns False → this assertion
        #   FAILS. Pins the executes-inside-double-quotes guard.
        """
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --body "$(git branch -D x)"'
        )

    def test_piped_to_shell_carrier_preserved_dangerous(self):
        """SECURITY NEGATIVE (piped-to-shell guard): when the whole command
        pipes to a shell, the strip is skipped entirely (the carrier text
        could be re-fed to a shell), so the command stays dangerous.

        # non-vacuity: remove the outer `if not piped_to_shell and not
        #   process_sub_to_shell:` guard (always run the carrier strip) →
        #   `gh issue create --title "git branch -D x" | bash` is stripped
        #   before the scan → is_dangerous returns False → this assertion
        #   FAILS. Pins the pipe-to-shell skip.
        """
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --title "git branch -D x" | bash'
        )

    # ---- NEGATIVE: non-carrier gh verbs are NOT exempted ----

    def test_gh_pr_edit_is_now_a_carrier(self):
        """#1129 R2 (OB1): `gh pr edit` is NOW an INTENTIONAL carrier — its
        --title/--body value is non-executing API prose (identical in kind to the
        already-carried `gh issue edit`), so a dangerous literal there is STRIPPED
        and the command is NOT flagged. The pre-R2 issue-edit/pr-edit asymmetry (an
        over-conservative over-block = cardinal-sin OB1) is intentionally removed.

        # non-vacuity: reverting the `pr edit` carrier (EDIT 1) leaves the literal
        #   unstripped → is_dangerous returns True → this assertion FAILS. Pins the
        #   pr-edit INCLUSION. The "non-carrier gh verb is NOT exempted" guardrail is
        #   pinned separately by the `gh pr review` sibling in
        #   test_merge_guard_1037_narrow.
        """
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('gh pr edit 5 --title "git branch -D x"')

    def test_gh_pr_merge_is_not_a_carrier(self):
        """SECURITY NEGATIVE: `gh pr merge` (a real destructive op) is not a
        carrier verb; a dangerous literal alongside it is not stripped and the
        command stays dangerous.
        """
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('gh pr merge 42 --subject "git branch -D x"')

    # ---- NEGATIVE: an unquoted dangerous token inside the span survives ----

    def test_unquoted_danger_token_in_span_survives(self):
        """SECURITY NEGATIVE: the inner strip touches only QUOTED flag-values.
        An unquoted dangerous op sitting inside the carrier span (not a flag
        value) is NOT stripped and stays caught.

        # non-vacuity: an inner strip that removed unquoted tokens (not just
        #   quoted flag-values) would strip this → is_dangerous returns False
        #   → FAILS. Pins the quoted-value-only scope of the inner strip.
        """
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command('gh issue create --title "safe" git push --force')

    # ---- REGRESSION GUARD: existing carrier 5 unchanged ----

    def test_git_commit_message_carrier_still_strips(self):
        """REGRESSION GUARD: the pre-existing `git commit -m` carrier (carrier
        5) is unchanged by the additive 7th carrier."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('git commit -m "msg about git branch -D x"')


class TestD3LadderPushToMain:
    """D3 (#933) symmetry sanity. The prose keyword-ladder that classified
    direct-push-to-main from QUESTION PROSE was DROPPED — the post hook now
    mints only from an embedded COMMAND (covered by the command-anchored
    bidirectional suite in test_merge_guard_auth_symmetry.py). What remains here
    is the read-side over-block sanity: an op-only force-push token never
    authorizes a cross-op command.
    """

    # (removed 5 methods that exercised the dropped prose extractor
    #  merge_guard_post.extract_context — direct-push/push-local/push-master
    #  prose classification, the merge-into-main negative, and the prose ladder
    #  precedence. Force-push detection from a COMMAND is covered by
    #  extract_command_context in test_merge_guard_auth_symmetry.py.)

    def test_force_push_token_does_not_authorize_non_force_push_command(self):
        """SYMMETRY SANITY: an over-matched force-push token (minted from
        direct-push prose) must NOT authorize a non-force-push command — the
        op_type guard at the PRE side blocks it. Proves the deliberately-broad
        D3 prose arm is harmless (over-match → over-block, never under-block).
        """
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "force-push"}}
        assert not _token_matches_command(token, "gh pr merge 42")


class TestD3PushToMainAuthorizationEndToEnd:
    """D3 (#933): direct-push-to-main is now minted only from an embedded
    COMMAND (the prose-classification mint was dropped); the end-to-end
    command-anchored mint is covered by test_merge_guard_auth_symmetry.py. What
    remains here is the read-side cross-op over-block sanity.
    """

    # (removed test_direct_push_to_main_prose_mints_a_token — it minted from the
    #  dropped prose extractor merge_guard_post.extract_context.)

    def test_push_to_main_token_does_not_authorize_cross_op(self):
        """Paired negative: a force-push token minted from direct-push-to-main
        prose does NOT authorize a cross-op `git branch -D x` command (the
        op_type axis does not match the branch-delete axis)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "force-push"}}
        assert not _token_matches_command(token, "git branch -D some-branch")


# =============================================================================
# #933 REMEDIATION (PR #1000 review findings) — M2/M3 quote-aware D2 span.
#
# The D2 carrier span body `[^&|;]*` was replaced with a quote-region-aware
# scanner:
#     gh<verb>  (?:[^&|;\n"']+ | "(?:[^"\\]|\\.)*" | '[^']*')*
# Three alternatives with DISJOINT first-character sets (bare run / balanced
# double-quote honoring \" / balanced single-quote, no escape). Effects:
#   M3 — a quoted value containing an internal ;/&/| is consumed ATOMICALLY
#        (the separator is inside the quote, not a span boundary) → the inner
#        strip sees the FULL value and strips it → benign title with internal
#        separators is no longer over-blocked.
#   M2 — a multi-line quoted title is consumed atomically (newline inside the
#        quote), while an UNQUOTED newline still terminates the span — fixing
#        the over-block a naive `[^&|;\n]*` body would have caused.
# The inner strip, the carve-out (close NOT a carrier verb), and the
# _has_command_substitution guard are UNCHANGED — INV-D2 (no real executing op
# is ever neutralized) is preserved: an unquoted separator/newline always
# terminates the span, and an UNBALANCED quote makes the span UNDER-consume
# (stop early) = over-block = safe, never under-block.
#
# NON-VACUITY uses OPPOSITE mutations for the two halves (the crux):
#   - 7.A multi-line/internal-sep POSITIVES are proven by an UNDER-CONSUME
#     mutation (span body → `[^&|;]*`): the value is truncated at its internal
#     separator → the inner strip misses it → is_dangerous flips True → FAIL.
#   - 7.B/7.C under-block + desync NEGATIVES are proven by an OVER-CONSUME
#     mutation (span body → `.*`): the span swallows past the unquoted
#     separator / unbalanced quote → the inner strip reaches the real op →
#     is_dangerous flips False → FAIL. (The old `[^&|;]*` body would NOT flip
#     these — it under-consumes too, so it keeps them dangerous == phantom-
#     green; that is why the negatives need the over-consume mutation, not the
#     `[^&|;]*` revert.)
# Each mutation + the RED set it produces is recorded per the `# non-vacuity:`
# convention; all mutations are restored byte-exact (git checkout HEAD --).
# =============================================================================


class TestD2QuoteAwareSpanRemediation:
    """#933 M2/M3 — the quote-aware carrier span (design §7.A-E).

    Dangerous literals live inside test-file string literals (never on a Bash
    line). The original 40-test suite's carve-out / compound negatives remain
    in TestD2GhCarrierStrip; this class adds the quote-aware-span pins.
    """

    # ---- 7.A — M3 / multi-line POSITIVES (a title whose quoted value contains
    #            an internal separator AND a dangerous literal AFTER that
    #            separator strips fully → NOT dangerous). ----
    #
    # NON-VACUITY DESIGN (load-bearing): each positive embeds a DANGEROUS
    # LITERAL positioned AFTER the internal `;`/`&`/`|`/newline. This is what
    # makes the test non-vacuous against the UNDER-CONSUME mutation: with the
    # `[^&|;]*` body the span TRUNCATES at the internal separator, leaving the
    # post-separator dangerous literal OUTSIDE the (now-unterminated) quoted
    # value → the inner strip cannot match it → the literal survives →
    # is_dangerous flips True → FAIL. A benign title (no dangerous literal,
    # e.g. "a; b") would stay `safe` under BOTH spans == phantom-green, so it
    # is NOT used here.
    #
    # non-vacuity (7.A — verified per-test, TWO under-consume mutations):
    #   - internal-`;`, internal-`&|`, danger-before-`;`, two-value: revert the
    #     span body to `[^&|;]*` → the value truncates at its internal
    #     separator → the post-separator dangerous literal is exposed → flips
    #     RED. (This is the SAME `[^&|;]*` that keeps the 7.B/7.C negatives
    #     green — the opposite-mutation crux.)
    #   - multiline: `[^&|;]*` consumes ACROSS newlines (it excludes only
    #     `&|;`), so it does NOT truncate a multi-line value → that test STAYS
    #     GREEN under `[^&|;]*` (would be phantom-green there). Its faithful
    #     mutation is the NAIVE FIX `[^&|;\n]*` (adds `\n` to the exclusion —
    #     the exact regression the architect's §7.A calls out): the span stops
    #     at the first newline → the later-line dangerous literal is exposed →
    #     flips RED. Verified: `[^&|;]*` → 3 of the 4 non-multiline strip-tests
    #     RED (multiline green); `[^&|;\n]*` → all 4 RED.

    def test_internal_semicolon_with_post_sep_danger_strips(self):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'gh issue create --title "ok; git branch -D real"'
        )

    def test_internal_amp_pipe_with_post_sep_danger_strips(self):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'gh issue create --title "fix & note | git push --force origin x"'
        )

    def test_multiline_with_post_newline_danger_strips(self):
        """The case a naive `[^&|;\\n]*` body would have REGRESSED (over-block):
        a multi-line title with a dangerous literal on a later line is consumed
        atomically and strips fully."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'gh issue create --title "line1\nrepro: git branch -D real\nline3"'
        )

    def test_danger_literal_before_internal_separator_strips(self):
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'gh issue create --title "repro: git branch -D x; then run"'
        )

    def test_two_value_internal_separators_with_danger_strip(self):
        """Two values, each with an internal separator AND a post-separator
        dangerous literal — BOTH strip fully (the two-value atomic-span case)."""
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'gh issue create -t "ok; git branch -D a" -b "ok | git push --force b"'
        )

    # ---- 7.B — UNDER-BLOCK NEGATIVES (INV-D2: a real op after an UNQUOTED
    #            separator MUST stay caught). ----
    #
    # non-vacuity (whole 7.B block): OVER-CONSUME whole-span over-strip —
    #   broaden the span body to `.*` AND blank the matched span → the span
    #   swallows the unquoted separator + the trailing op → is_dangerous flips
    #   False → FAIL. Verified RED. The `[^&|;]*` revert does NOT flip these
    #   (it also stops at the unquoted separator == phantom-green) — over-
    #   consume is required.
    #   NEWLINE caveat: `.*` does NOT cross a newline (no re.DOTALL), so the
    #   newline-tail case (test_under_block_newline_tail) needs the
    #   newline-crossing over-consume `[\s\S]*` to flip — verified RED under
    #   that mutation. (Plain `.*` leaves it green because the span still stops
    #   at the unquoted `\n`.)

    def test_under_block_amp_amp_tail(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --title "x" && git branch -D real'
        )

    def test_under_block_semicolon_tail(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --title "x" ; git branch -D real'
        )

    def test_under_block_pipe_tail(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --title "x" | git branch -D real'
        )

    def test_under_block_newline_tail(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --title "x"\ngit branch -D real'
        )

    def test_under_block_background_amp_tail(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --title "x" & git branch -D real'
        )

    def test_under_block_unquoted_op_in_span(self):
        """An UNQUOTED dangerous op inside the span (not a flag value) is never
        touched by the inner strip (quoted-value-scoped) → stays caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --title "safe" git push --force origin x'
        )

    def test_under_block_single_destructive_after_carrier_not_compound(self):
        """Honest-mistake ≥2: `gh issue create --title "x" && git branch -D real` has
        ONE destructive leg (branch-delete) → NOT compound. The branch-delete tail
        survives the carrier strip and stays is_dangerous-gated (the single-op gate
        catches it), so it is not under-blocked — it is simply not >=2-compound."""
        from merge_guard_pre import is_compound_destructive_command, is_dangerous_command

        cmd = 'gh issue create --title "x" && git branch -D real'
        assert is_compound_destructive_command(cmd) is False
        assert is_dangerous_command(cmd) is True

    # ---- 7.C — DESYNC NEGATIVES (escaped/unbalanced/mismatched quotes MUST
    #            NOT smuggle an op past the span). ----
    #
    # non-vacuity (whole 7.C block): OVER-CONSUME-PAST-UNBALANCED-QUOTE
    #   whole-span over-strip — broaden the span body so it consumes past an
    #   unbalanced quote (body → `.*` AND blank the matched span) → the span
    #   swallows the trailing op → is_dangerous flips False → these FAIL.
    #   Verified RED. The as-built body stops at the unbalanced quote (under-
    #   consume = over-block = safe), so a real op after it always survives.
    #   NEWLINE caveat: the unbalanced-DQ-then-NEWLINE case
    #   (test_desync_unbalanced_dq_then_newline_op) needs the newline-crossing
    #   `[\s\S]*` over-consume to flip (plain `.*` stops at the `\n`) —
    #   verified RED under that mutation.

    def test_desync_unbalanced_dq_then_amp_op(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --title "open && git branch -D real'
        )

    def test_desync_unbalanced_dq_then_newline_op(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --title "open\ngit branch -D real'
        )

    def test_desync_escaped_quote_then_semicolon_op(self):
        r"""An escaped `\"` keeps the string open in BOTH the regex
        (`(?:[^"\\]|\\.)`) and bash, so the `;`-separated op is outside any
        closed quote and survives."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --title "a\\" ; git branch -D real'
        )

    def test_desync_unbalanced_sq_then_amp_op(self):
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            "gh issue create --title 'open && git branch -D real"
        )

    def test_desync_mixed_quote_then_amp_op(self):
        """A single quote inside a BALANCED double-quoted value (`"a'b"`) is
        ordinary content; the following `&&` op is unquoted → caught."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command(
            'gh issue create --title "a\'b" && git branch -D real'
        )

    def test_desync_genuinely_quoted_op_is_neutralized(self):
        """CORRECTNESS pin: an op GENUINELY inside a balanced quoted value (no
        internal separator) IS neutralized — bash also treats it as inert
        title text. (NOT a hole.)

        # non-vacuity: this value has NO internal separator, so the UNDER-
        #   CONSUME `[^&|;]*` span mutation does NOT flip it (it consumes the
        #   whole value either way) — it stays green there. Its non-vacuity is
        #   the CARRIER-PRESENCE proof shared with the original suite
        #   (TestD2GhCarrierStrip.test_issue_create_title_with_danger_literal_
        #   not_dangerous): revert the entire 7th carrier (remove the strip
        #   step) → the literal is no longer stripped → is_dangerous flips True
        #   → FAILS. This pin documents the M2/M3 span did not REGRESS the
        #   baseline single-value strip; the span-mutation tests above carry
        #   the M2/M3-specific non-vacuity.
        """
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command(
            'gh issue create --title "git branch -D x"'
        )

    # ---- 7.D — Carve-out REGRESSION GUARD (unchanged by the span change). ----

    def test_carveout_pr_close_still_dangerous(self):
        """Behavioral (robust, but vacuous w.r.t. the carrier — see the
        mechanism-level pin below)."""
        from merge_guard_pre import is_dangerous_command

        assert is_dangerous_command("gh pr close 42 --delete-branch")

    def test_carveout_pr_close_value_survives_strip(self):
        """Mechanism-level: a quoted value on a `gh pr close` command is NOT
        stripped (close is excluded from the carrier verbs), even under the
        new quote-aware span.

        # non-vacuity: broaden the carrier verb alternation to include `close`
        #   (`pr\\s+(?:create|close)`) → the value is stripped → this assertion
        #   FAILS. (Exclusion-guard non-vacuity, carried over from the original
        #   carve-out pin.)
        """
        from merge_guard_pre import _strip_non_executable_content

        cmd = 'gh pr close 42 --delete-branch --body "git branch -D x"'
        assert '"git branch -D x"' in _strip_non_executable_content(cmd)

    def test_carveout_pr_edit_is_now_a_carrier(self):
        # #1129 R2 (OB1): `gh pr edit` is NOW an intentional carrier (joins gh issue
        # edit as a non-executing --title/--body prose carrier), so a dangerous literal
        # in its value is STRIPPED and NOT flagged. Reverting EDIT 1 flips this True →
        # the assert FAILS (non-vacuous). The non-carrier-verb guardrail is pinned by
        # the `gh pr review` sibling in test_merge_guard_1037_narrow.
        from merge_guard_pre import is_dangerous_command

        assert not is_dangerous_command('gh pr edit 5 --title "git branch -D x"')

    # ---- 7.E — ReDoS guard for the span ITSELF (the quote-aware body's own
    #            linear profile; distinct from the M1 D3 ladder ReDoS). ----

    def test_span_redos_linear_profile(self):
        """The quote-aware span `(?:[^&|;\\n"']+|"(?:...)*"|'[^']*')*` has three
        DISJOINT-first-char alternatives, so the nested `*` cannot backtrack
        ambiguously — the match is LINEAR. A 40 KB unterminated-double-quote
        input (the classic `(a+)*` catastrophic trigger) completes well under
        a generous bound.

        # non-vacuity: pins that the span does not REGRESS into catastrophic
        #   backtracking. The generous bound (100 ms vs the measured sub-ms)
        #   is revert-proving without timing flakiness.
        """
        import time

        from merge_guard_pre import _strip_non_executable_content

        worst = 'gh issue create --title "' + "a" * 40000
        start = time.perf_counter()
        _strip_non_executable_content(worst)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.1, f"span strip took {elapsed*1000:.1f}ms (ReDoS?)"


# =============================================================================
# #933 REMEDIATION (PR #1000 finding M1) — D3 ladder ReDoS perf bound.
#
# The force-push ladder arm in extract_context (merge_guard_post.py) carried a
# nested-greedy shape `push\b(?:.*\bto\b)?.*\b(?:main|master)\b` — two `.*`
# runs separated by an optional `\bto\b` group — which catastrophically
# backtracks on an input full of "to" tokens that never reaches main/master.
# The fix replaces it with a single lazy `push\b.*?\b(?:main|master)\b` (and
# the sibling `direct\s+push.*?...`), eliminating the ambiguous nested
# quantifier so the match is linear.
# =============================================================================


# (removed class TestD3LadderReDoSPerfBound — superseded by the command-anchored
#  bidirectional suite in test_merge_guard_auth_symmetry.py;
#  it exercised the dropped prose classifier extract_context.)
