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
    re.compile(r"\bgh\s+pr\s+merge\b"),
    re.compile(r"\bgit\s+push\s+.*--force\b"),
    re.compile(r"\bgit\s+push\s+.*-f\b"),
    re.compile(r"\bgit\s+branch\s+.*-D\b"),
    re.compile(r"\bgit\s+branch\s+.*--delete\s+--force\b"),
    re.compile(r"\bgit\s+branch\s+--force\s+--delete\b"),
    re.compile(r"\bgit\s+push\s+-[a-zA-Z]*f"),
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


def find_valid_token(token_dir: Path | None = None) -> dict | None:
    """Find a valid (unexpired) authorization token.

    Also cleans up any expired token files.

    Args:
        token_dir: Override token directory (for testing)

    Returns:
        The token data dict if a valid token exists, None otherwise
    """
    if token_dir is None:
        token_dir = TOKEN_DIR

    now = time.time()
    valid_token = None
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

            # Valid token found
            valid_token = token_data

        except (json.JSONDecodeError, OSError, KeyError):
            # Corrupted token — clean up
            _safe_remove(token_path)

    return valid_token


def _safe_remove(path: str):
    """Remove a file, ignoring errors if it doesn't exist."""
    try:
        os.unlink(path)
    except OSError:
        pass


def check_merge_authorization(command: str, token_dir: Path | None = None) -> str | None:
    """Check if a dangerous command is authorized.

    Args:
        command: The bash command to check
        token_dir: Override token directory (for testing)

    Returns:
        Error message if blocked, None if allowed
    """
    if not is_dangerous_command(command):
        return None

    token = find_valid_token(token_dir)
    if token is not None:
        return None

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
        # Don't block on unexpected errors
        print(f"Hook warning (merge_guard_pre): {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
