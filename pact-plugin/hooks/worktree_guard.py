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
    """Check if path is in the allow-list (always permitted).

    Uses path component matching instead of substring matching to avoid
    false positives (e.g., a directory named 'mydocs' matching '/docs/').
    """
    p = Path(file_path)
    parts = p.parts
    name = p.name
    for pattern in ALLOW_PATTERNS:
        clean = pattern.strip("/")
        # Match as path component (e.g., ".claude", "docs") or filename (e.g., "CLAUDE.md")
        if clean in parts or name == clean:
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


def _find_project_root(worktree_path: str) -> str | None:
    """
    Find the project root for a worktree by locating the .worktrees ancestor.

    Args:
        worktree_path: Resolved worktree path

    Returns:
        Project root path, or None if not found
    """
    worktree_p = Path(worktree_path)
    for parent in worktree_p.parents:
        worktrees_dir = parent / ".worktrees"
        if worktrees_dir.is_dir():
            return str(parent)
    return None


def _suggest_worktree_path(file_path: str, worktree_path: str) -> str | None:
    """
    Compute the corrected worktree path for a file outside the worktree.

    Attempts to find the project root by looking for the .worktrees ancestor,
    validates that the file belongs to the same project, then replaces the
    project root prefix with the worktree path.

    Args:
        file_path: Path of the file being edited (outside worktree)
        worktree_path: Active worktree path

    Returns:
        Suggested corrected path, or None if unable to compute
    """
    try:
        resolved_file = str(Path(file_path).resolve())
        resolved_worktree = str(Path(worktree_path).resolve())

        # Find project root from worktree path
        project_root = _find_project_root(resolved_worktree)
        if project_root:
            # Only suggest if the file is under the SAME project root.
            # This prevents false matches from nested worktree projects.
            if resolved_file.startswith(project_root + "/"):
                relative = resolved_file[len(project_root) + 1:]
                return str(Path(resolved_worktree) / relative)
            # File is not under this project root — don't suggest
            return None

        # Fallback: try to find common path segments between file and worktree.
        # If worktree is /a/b/.worktrees/feat/x and file is /a/b/src/foo.py,
        # the relative part after the common ancestor is src/foo.py.
        #
        # Validate: the common ancestor must be a plausible project root
        # (contains .git, .worktrees, or similar). Without this check, two
        # unrelated paths like /usr/local/bin/tool and /usr/share/lib/x would
        # match on /usr and produce a nonsensical suggestion.
        file_parts = Path(resolved_file).parts
        wt_parts = Path(resolved_worktree).parts
        common_len = 0
        for i, (fp, wp) in enumerate(zip(file_parts, wt_parts)):
            if fp == wp:
                common_len = i + 1
            else:
                break

        if common_len > 1:  # Must share more than just "/" root
            common_ancestor = Path(*file_parts[:common_len])
            # Validate: common ancestor looks like a project directory
            is_project_dir = (
                (common_ancestor / ".git").exists()
                or (common_ancestor / ".worktrees").exists()
                or (common_ancestor / "CLAUDE.md").exists()
            )
            if is_project_dir:
                relative = str(Path(*file_parts[common_len:]))
                return str(Path(resolved_worktree) / relative)
    except (ValueError, OSError):
        pass

    return None


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
        suggestion = _suggest_worktree_path(file_path, worktree_path)
        msg = (
            f"Edit blocked: {file_path} is outside the active worktree "
            f"at {worktree_path}."
        )
        if suggestion:
            msg += f"\nDid you mean: {suggestion}"
        return msg

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
