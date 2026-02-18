#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/worktree_guard.py
Summary: PreToolUse hook matching Edit|Write that blocks edits to application
         code outside the active worktree boundary.
Used by: hooks.json PreToolUse hook (matcher: Edit|Write)

Only active when PACT_WORKTREE_PATH env var is set (by /PACT:worktree-setup).
Complete no-op otherwise.

Input: JSON from stdin with tool_input.file_path
Output: JSON with hookSpecificOutput.permissionDecision if blocking
"""

import json
import sys
import os
from pathlib import Path

# Paths always allowed regardless of worktree
ALLOW_PATTERNS = [
    "/.claude/",
    "/docs/",
    "CLAUDE.md",
    ".gitignore",
]

# Directories that indicate application code
APP_CODE_DIRS = ["src/", "lib/", "app/", "test/", "tests/", "scripts/"]

# File extensions that indicate application code
APP_CODE_EXTENSIONS = {
    ".py", ".ts", ".js", ".tsx", ".jsx", ".rb", ".go", ".rs",
    ".java", ".sh", ".yml", ".yaml", ".tf", ".sql",
}


def is_allowed_path(file_path: str) -> bool:
    """Check if path is in the allow-list (always permitted)."""
    for pattern in ALLOW_PATTERNS:
        if pattern in file_path:
            return True
    return False


def is_application_code(file_path: str) -> bool:
    """Heuristic: is this file application code?"""
    # Check directory indicators
    for dir_pattern in APP_CODE_DIRS:
        if f"/{dir_pattern}" in file_path:
            return True

    # Check extension
    suffix = Path(file_path).suffix.lower()
    return suffix in APP_CODE_EXTENSIONS


def check_worktree_boundary(file_path: str, worktree_path: str) -> str | None:
    """
    Check if a file edit is within the worktree boundary.

    Args:
        file_path: Path of the file being edited
        worktree_path: Active worktree path (empty = inactive)

    Returns:
        Error message if blocked, None if allowed
    """
    if not worktree_path:
        return None  # No worktree active, no-op

    # Always allow certain paths
    if is_allowed_path(file_path):
        return None

    # Check if inside worktree
    try:
        resolved_file = str(Path(file_path).resolve())
        resolved_worktree = str(Path(worktree_path).resolve())
        if resolved_file.startswith(resolved_worktree):
            return None  # Inside worktree, OK
    except (ValueError, OSError):
        return None  # Can't resolve, allow by default

    # Outside worktree — only block if it's application code
    if is_application_code(file_path):
        return (
            f"File is outside worktree boundary: {file_path}\n"
            f"Expected prefix: {worktree_path}\n"
            f"Edit application code only within the worktree."
        )

    return None  # Non-app-code outside worktree is fine


def main():
    worktree_path = os.environ.get("PACT_WORKTREE_PATH", "")
    if not worktree_path:
        sys.exit(0)  # No worktree active, complete no-op

    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    file_path = input_data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        sys.exit(0)

    error = check_worktree_boundary(file_path, worktree_path)

    if error:
        output = {
            "hookSpecificOutput": {
                "permissionDecision": "deny",
                "permissionDecisionReason": error
            }
        }
        print(json.dumps(output))
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
