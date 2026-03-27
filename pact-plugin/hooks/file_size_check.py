#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/file_size_check.py
Summary: PostToolUse hook that alerts when files exceed line count thresholds.
Used by: Claude Code settings.json PostToolUse hook (Edit, Write tools)

Monitors file sizes after edits and provides guidance when files grow too large,
encouraging SOLID/DRY principles and architectural refactoring.

Input: JSON from stdin with tool_name, tool_input, tool_output
Output: JSON with `hookSpecificOutput.additionalContext` when file exceeds threshold
"""

import json
import os
import sys

from shared.error_output import hook_error_json

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Line count thresholds
WARNING_THRESHOLD = 600  # Trigger guidance at this line count
CRITICAL_THRESHOLD = 800  # More urgent guidance

# File extensions to check (source code files)
CHECKED_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".rb", ".go", ".java",
    ".rs", ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt",
    ".scala", ".vue", ".svelte", ".php"
}

# Paths to exclude from checks
EXCLUDED_PATHS = [
    "__pycache__",
    "node_modules",
    ".git/",
    "dist/",
    "build/",
    "vendor/",
    ".venv/",
    "venv/",
]


def is_excluded_path(file_path: str) -> bool:
    """Check if the file path should be excluded from size checks."""
    for pattern in EXCLUDED_PATHS:
        if pattern in file_path:
            return True
    return False


def should_check_file(file_path: str) -> bool:
    """Determine if this file type should be checked for size."""
    _, ext = os.path.splitext(file_path)
    return ext.lower() in CHECKED_EXTENSIONS


def count_lines(file_path: str) -> int:
    """Count the number of lines in a file."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return sum(1 for _ in f)
    except (IOError, OSError):
        return 0


def format_guidance(file_path: str, line_count: int) -> str:
    """Format the refactoring guidance message."""
    filename = os.path.basename(file_path)

    if line_count >= CRITICAL_THRESHOLD:
        urgency = "⚠️ CRITICAL"
        intro = f"File `{filename}` is now {line_count} lines - well above the 600-line maintainability threshold."
    else:
        urgency = "📏 FILE SIZE"
        intro = f"File `{filename}` has grown to {line_count} lines, exceeding the 600-line maintainability threshold."

    return (
        f"{urgency}: {intro}\n\n"
        "Consider applying:\n"
        "- **SOLID principles**: Single Responsibility - does this file do one thing?\n"
        "- **DRY**: Are there duplicated patterns that could be extracted?\n"
        "- **Modular design**: Can logical sections become separate modules?\n\n"
        "💡 Recommendation: Use the **pact-architect** agent to analyze this file and "
        "design a refactoring strategy that breaks it into smaller, focused components."
    )


def main():
    """Main entry point for the PostToolUse hook."""
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Only process Edit and Write tools
        if tool_name not in ("Edit", "Write"):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        file_path = tool_input.get("file_path", "")
        if not file_path:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Skip excluded paths and non-source files
        if is_excluded_path(file_path):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        if not should_check_file(file_path):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Check if file exists and count lines
        if not os.path.isfile(file_path):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        line_count = count_lines(file_path)

        # Only output guidance if threshold exceeded
        if line_count < WARNING_THRESHOLD:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": format_guidance(file_path, line_count)
            }
        }
        print(json.dumps(output))
        sys.exit(0)

    except Exception as e:
        # Don't block on errors
        print(f"Hook warning (file_size_check): {e}", file=sys.stderr)
        print(hook_error_json("file_size_check", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
