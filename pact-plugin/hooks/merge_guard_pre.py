#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/merge_guard_pre.py
Summary: PreToolUse hook matching Bash — blocks merge, force push, and branch
         delete commands unless a valid authorization token exists.
Used by: hooks.json PreToolUse hook (matcher: Bash)

This hook is part of the merge guard system. It checks for a valid token
written by the companion hook (merge_guard_post.py) before allowing dangerous
git operations. If no valid token exists, the command is blocked with a message
directing the user to confirm via AskUserQuestion first.

Input: JSON from stdin with tool_input containing the command
Output: JSON with hookSpecificOutput.permissionDecision if blocking
"""

import glob
import json
import os
import re
import sys
import time
from pathlib import Path

# Token TTL in seconds (must match merge_guard_post.py)
TOKEN_TTL = 300

# Directory for token files
TOKEN_DIR = Path.home() / ".claude"

# Token file prefix
TOKEN_PREFIX = "merge-authorized-"

# Patterns for dangerous commands
DANGEROUS_PATTERNS = [
    # PR merge via gh CLI
    re.compile(r"\bgh\s+pr\s+merge\b"),
    # Force push (excludes --force-with-lease which is a safer alternative)
    re.compile(r"\bgit\s+(?:-c\s+\S+\s+)*push\s+.*--force(?!-with-lease)\b"),
    re.compile(r"\bgit\s+(?:-c\s+\S+\s+)*push\s+.*-f\b"),
    re.compile(r"\bgit\s+(?:-c\s+\S+\s+)*push\s+-[a-zA-Z]*f"),
    # Force branch deletion
    re.compile(r"\bgit\s+branch\s+.*-D\b"),
    re.compile(r"\bgit\s+branch\s+.*--delete\s+--force\b"),
    re.compile(r"\bgit\s+branch\s+--force\s+--delete\b"),
    # API-based merge bypasses
    re.compile(r"\bgh\s+api\b.*merge", re.IGNORECASE),
    re.compile(r"\bcurl\b.*api.*merge", re.IGNORECASE),
    # Direct push to default branch (bypasses PR merge)
    re.compile(r"\bgit\s+(?:-c\s+\S+\s+)*push\s+\S+\s+HEAD:main\b"),
    re.compile(r"\bgit\s+(?:-c\s+\S+\s+)*push\s+\S+\s+HEAD:master\b"),
]


def is_dangerous_command(command: str) -> bool:
    """Check if a bash command is a dangerous git operation.

    Args:
        command: The bash command string

    Returns:
        True if the command matches a dangerous pattern
    """
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(command):
            return True
    return False


def find_valid_token(token_dir: Path | None = None) -> tuple[dict, str] | tuple[None, None]:
    """Find a valid (unexpired) authorization token for the current session.

    Also cleans up any expired token files. If CLAUDE_SESSION_ID is set, only
    tokens from the current session are accepted. If not set, any valid token
    is accepted (graceful degradation).

    Args:
        token_dir: Override token directory (for testing)

    Returns:
        Tuple of (token_data, token_path) if a valid token exists,
        (None, None) otherwise
    """
    if token_dir is None:
        token_dir = TOKEN_DIR

    current_session = os.environ.get("CLAUDE_SESSION_ID", "")

    now = time.time()
    valid_token = None
    valid_path = None
    token_pattern = str(token_dir / f"{TOKEN_PREFIX}*")

    for token_path in glob.glob(token_pattern):
        try:
            with open(token_path, "r") as f:
                token_data = json.load(f)

            expires_at = token_data.get("expires_at", 0)

            if not isinstance(expires_at, (int, float)) or expires_at <= 0:
                # Invalid token data — clean up
                _safe_remove(token_path)
                continue

            if expires_at < now:
                # Expired — clean up
                _safe_remove(token_path)
                continue

            # Session scoping: if both sides have session IDs, they must match
            token_session = token_data.get("session_id", "")
            if current_session and token_session and current_session != token_session:
                # Token from a different session — skip (don't clean up,
                # it may be valid for its own session)
                continue

            # Valid token found
            valid_token = token_data
            valid_path = token_path

        except (json.JSONDecodeError, OSError, KeyError, AttributeError, TypeError):
            # Corrupted or malformed token — clean up
            _safe_remove(token_path)

    return valid_token, valid_path


def _safe_remove(path: str):
    """Remove a file, ignoring errors if it doesn't exist."""
    try:
        os.unlink(path)
    except OSError:
        pass


def _token_matches_command(token: dict, command: str) -> bool:
    """Check if a token's context is consistent with the command being executed.

    If the token has specific context (PR number, branch name), verify the command
    matches. If parsing is ambiguous or no context is available, allow through
    to avoid false negatives.

    Args:
        token: Token data dict with optional context fields
        command: The bash command being authorized

    Returns:
        True if the command is consistent with the token's context (or ambiguous)
    """
    context = token.get("context", {})
    if not isinstance(context, dict):
        return True  # Malformed context — allow through

    pr_number = context.get("pr_number")
    branch = context.get("branch")

    # If token has a PR number, check gh pr merge commands match
    if pr_number:
        pr_merge_match = re.search(r"\bgh\s+pr\s+merge\s+(\d+)", command)
        if pr_merge_match:
            return pr_merge_match.group(1) == str(pr_number)

    # If token has a branch, check branch deletion commands match
    if branch:
        branch_d_match = re.search(r"\bgit\s+branch\s+.*-D\s+(\S+)", command)
        if branch_d_match:
            return branch_d_match.group(1) == branch
        branch_delete_match = re.search(
            r"\bgit\s+branch\s+.*--delete\s+(?:--force\s+)?(\S+)", command
        )
        if branch_delete_match:
            return branch_delete_match.group(1) == branch

    # No specific context to validate against, or command type doesn't match
    # context type — allow through (ambiguous is permissive)
    return True


def check_merge_authorization(command: str, token_dir: Path | None = None) -> str | None:
    """Check if a dangerous command is authorized.

    Tokens are single-use: once a token authorizes a command, it is consumed
    (deleted) so that each approval authorizes exactly one operation. The token's
    context is validated against the command to ensure the approved operation
    matches what is being executed.

    Args:
        command: The bash command to check
        token_dir: Override token directory (for testing)

    Returns:
        Error message if blocked, None if allowed
    """
    if not is_dangerous_command(command):
        return None

    token, token_path = find_valid_token(token_dir)
    if token is not None:
        if _token_matches_command(token, command):
            # Consume the token — one approval = one operation
            _safe_remove(token_path)
            return None
        else:
            # Token exists but doesn't match this command — don't consume it,
            # block the mismatched command
            return (
                "Authorization token exists but does not match this operation. "
                "Use AskUserQuestion to get approval for this specific operation."
            )

    return (
        "Merge/force-push/branch-delete requires user approval via AskUserQuestion. "
        "Use AskUserQuestion to confirm with the user before proceeding."
    )


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            sys.exit(0)

        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        if not command:
            sys.exit(0)

        error = check_merge_authorization(command)

        if error:
            output = {
                "hookSpecificOutput": {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": error,
                }
            }
            print(json.dumps(output))
            sys.exit(2)

        sys.exit(0)

    except Exception as e:
        # Security guard fails closed — block on unexpected errors
        print(f"Hook error (merge_guard_pre): {e}", file=sys.stderr)
        try:
            output = {
                "hookSpecificOutput": {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Merge guard internal error — blocking for safety. "
                        "If this persists, check the merge guard hooks."
                    ),
                }
            }
            print(json.dumps(output))
        except Exception:
            pass  # If even the deny output fails, fail silently
        sys.exit(2)


if __name__ == "__main__":
    main()
