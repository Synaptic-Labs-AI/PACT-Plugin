#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/merge_guard_post.py
Summary: PostToolUse hook matching AskUserQuestion — writes a short-lived
         authorization token when the user approves a merge, close (with branch
         deletion), force push, or branch delete question.
Used by: hooks.json PostToolUse hook (matcher: AskUserQuestion)

This hook is part of the merge guard system. When AskUserQuestion is used to
confirm a merge, close (with branch deletion), force push, or branch deletion,
and the user answers affirmatively, a token file is written to ~/.claude/. The
companion hook (merge_guard_pre.py) checks for this token before allowing
dangerous commands.

Input: JSON from stdin with tool_input (AskUserQuestion questions array) and tool_output (answers dict)
Output: None (side effect: writes token file on approval)
"""

import json
import os
import re
import sys
import time
from pathlib import Path

# Shared constants and cleanup — single source of truth for both hooks
sys.path.insert(0, str(Path(__file__).parent))
from shared.error_output import hook_error_json
from shared.merge_guard_common import (
    TOKEN_TTL,
    TOKEN_DIR,
    cleanup_consumed_tokens as _cleanup_consumed_tokens,
)

# When the hook allows a command (exits 0), output this JSON so the Claude Code
# UI suppresses the hook display instead of showing "hook error (No output)".
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Keywords that indicate a merge-related question
MERGE_KEYWORDS = re.compile(
    r"merge|close\s+(?:pr|pull\s*request)|(?:pr|pull\s*request)\s+close|"
    r"gh\s+pr\s+close|force[\s-]?push|delete[\s-]?branch|branch[\s-]?-[dD]|"
    r"branch\s+--delete|--force|git\s+push\s+-f",
    re.IGNORECASE,
)

# Patterns that indicate an affirmative user answer
AFFIRMATIVE_PATTERNS = re.compile(
    r"^(y|yes|yeah|yep|sure|ok|okay|confirm|approved?|go\s*ahead|do\s*it|proceed)\b",
    re.IGNORECASE,
)


def is_merge_question(question: str) -> bool:
    """Check if the question text is about a merge-related operation.

    Args:
        question: The question text from AskUserQuestion

    Returns:
        True if the question contains merge-related keywords
    """
    return bool(MERGE_KEYWORDS.search(question))


def is_affirmative(answer: str) -> bool:
    """Check if the user's answer is affirmative.

    Args:
        answer: The user's response text

    Returns:
        True if the answer indicates approval
    """
    return bool(AFFIRMATIVE_PATTERNS.search(answer.strip()))


def extract_context(question: str) -> dict:
    """Extract operation context from the question text.

    Attempts to find PR numbers, branch names, and operation type from the
    question.

    Args:
        question: The question text

    Returns:
        Dict with extracted context fields including operation_type
    """
    context = {"question_snippet": question[:200]}

    # Try to extract PR number
    pr_match = re.search(r"#(\d+)|PR\s*(\d+)|pull\s*request\s*(\d+)", question, re.IGNORECASE)
    if pr_match:
        context["pr_number"] = pr_match.group(1) or pr_match.group(2) or pr_match.group(3)

    # Try to extract branch name
    branch_match = re.search(
        r"branch\s+['\"]?([a-zA-Z0-9/_.-]+)['\"]?|"
        r"merge\s+['\"]?([a-zA-Z0-9/_.-]+)['\"]?",
        question,
        re.IGNORECASE,
    )
    if branch_match:
        context["branch"] = branch_match.group(1) or branch_match.group(2)

    # Detect operation type for token scoping
    question_lower = question.lower()
    if re.search(r"\bclose\b.*(?:pr|pull\s*request)|(?:pr|pull\s*request).*\bclose\b|gh\s+pr\s+close", question_lower):
        context["operation_type"] = "close"
    elif re.search(r"\bmerge\b", question_lower):
        context["operation_type"] = "merge"

    return context


def write_token(context: dict, token_dir: Path | None = None) -> str | None:
    """Write an authorization token file.

    Args:
        context: Operation context to include in the token
        token_dir: Override token directory (for testing)

    Returns:
        Path to the created token file, or None on failure
    """
    if token_dir is None:
        token_dir = TOKEN_DIR

    # Clean up stale .consumed token files from prior operations
    _cleanup_consumed_tokens(token_dir)

    now = time.time()
    timestamp = int(now)

    # Include session ID for cross-session scoping (graceful degradation)
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")

    token_data = {
        "created_at": now,
        "expires_at": now + TOKEN_TTL,
        "context": context,
    }
    if session_id:
        token_data["session_id"] = session_id

    token_path = token_dir / f"merge-authorized-{timestamp}"

    try:
        # Write with secure permissions using os.open for atomic creation
        fd = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(token_data, f, indent=2)
        except Exception:
            # fd is already closed by fdopen on failure, but file may exist
            try:
                token_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return str(token_path)
    except FileExistsError:
        # Extremely unlikely race — try with microsecond suffix
        token_path = token_dir / f"merge-authorized-{timestamp}-{int(now * 1000) % 1000}"
        try:
            fd = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(token_data, f, indent=2)
            except Exception:
                try:
                    token_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            return str(token_path)
        except OSError:
            return None
    except OSError:
        return None


def main():
    """Main entry point for the PostToolUse hook."""
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        tool_input = input_data.get("tool_input", {})
        tool_output = input_data.get("tool_output", {})

        # Extract question from AskUserQuestion schema:
        # tool_input: {"questions": [{"question": "...", ...}]}
        if not isinstance(tool_input, dict):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        questions = tool_input.get("questions", [])
        if isinstance(questions, list) and len(questions) > 0:
            first_q = questions[0]
            question = first_q.get("question", "") if isinstance(first_q, dict) else ""
        else:
            question = ""

        # Extract answer from AskUserQuestion schema:
        # tool_output: {"answers": {"question_text": "answer_text"}, ...}
        if not isinstance(tool_output, dict):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        answers = tool_output.get("answers", {})
        if isinstance(answers, dict) and answers:
            # Look up answer by exact question text; fall back to first value
            answer = str(answers.get(question, next(iter(answers.values()), "")))
        else:
            answer = ""

        # Only act on merge-related questions with affirmative answers
        if question and is_merge_question(question) and answer and is_affirmative(answer):
            context = extract_context(question)
            token_path = write_token(context)
            if token_path:
                print(
                    f"Merge authorization token written: {token_path}",
                    file=sys.stderr,
                )

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        # Never block on errors — this is an observer hook
        print(f"Hook warning (merge_guard_post): {e}", file=sys.stderr)
        print(hook_error_json("merge_guard_post", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
