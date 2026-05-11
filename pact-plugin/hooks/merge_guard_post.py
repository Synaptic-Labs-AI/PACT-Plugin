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

Input: JSON from stdin with tool_input (AskUserQuestion questions array) and tool_response (answers dict)
Output: None (side effect: writes token file on approval)
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import shared.pact_context as pact_context
from shared.pact_context import get_session_id

# Shared constants and cleanup — single source of truth for both hooks
sys.path.insert(0, str(Path(__file__).parent))
from shared.error_output import hook_error_json

from shared.merge_guard_common import (
    TOKEN_TTL,
    TOKEN_DIR,
    cleanup_consumed_tokens as _cleanup_consumed_tokens,
    detect_command_operation_type,
)
from shared.tool_response import extract_tool_response


# Regex for a quoted-command region inside question prose. Tries
# backticks first (most common), then single quotes, then double quotes.
# Captures the content. The question prose is short and single-line in
# practice; no DOTALL needed.
#
# When the AskUserQuestion text embeds the literal command in a quoted
# region (e.g., `gh pr merge 42` or 'git branch -D feat/x'), the
# read-side classifier shared.detect_command_operation_type is applied
# to the embedded command — guaranteeing the write-side and read-side
# classifications agree on the SAME input. This is the canonical
# operation-type path; the keyword-ladder fallback in extract_context()
# only fires when no quoted region matched (#720 Bug B).
_QUOTED_COMMAND_RE = re.compile(
    r"`([^`]+)`"        # backticks: `git push origin main`
    r"|'([^']+)'"       # single quotes: 'git push origin main'
    r'|"([^"]+)"'       # double quotes: "git push origin main"
)


def _classify_from_quoted_command(question: str) -> str | None:
    """Find a quoted command region in question prose and classify it
    via shared.detect_command_operation_type.

    Returns the op_type literal (merge/close/force-push/branch-delete)
    or None if no quoted region matched a destructive shape.

    Tries each quoted region in document order; returns the first
    non-None classification. This makes embedding the command-literal in
    the question prose the AUTHORITATIVE classifier path. The keyword
    ladder in extract_context() is the fallback for questions that omit
    the embedded command (legacy or non-conforming orchestrator prose).
    """
    for match in _QUOTED_COMMAND_RE.finditer(question):
        candidate = match.group(1) or match.group(2) or match.group(3)
        if not candidate:
            continue
        op_type = detect_command_operation_type(candidate)
        if op_type is not None:
            return op_type
    return None

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

    Symmetry with shared.detect_command_operation_type: if the question
    embeds a literal command in a quoted region (backticks, single
    quotes, or double quotes), the SAME classifier the read-side uses is
    applied — guaranteeing bidirectional agreement when the convention
    is honored. Falls back to a keyword-ladder classification for
    legacy/non-conforming prose (#720 Bug B).

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

    # Operation type — canonical path: try a quoted-command region first
    # and delegate to the shared read-side classifier. When this returns
    # non-None, bidirectional write/read symmetry is guaranteed by
    # construction (both sides classify the SAME literal command).
    op_from_quoted = _classify_from_quoted_command(question)
    if op_from_quoted is not None:
        context["operation_type"] = op_from_quoted
    else:
        # Fallback keyword ladder for prose that omits the quoted command.
        # Order: close → force-push → branch-delete → merge.
        # Unambiguous syntactic features (`branch -D`, `--force`, `-f`)
        # precede the fuzzy bare-word `\bmerge\b`, so prose mentioning
        # both (e.g., "force-delete the merged feature branch") classifies
        # by the syntactic feature, not by the appearance of "merged".
        question_lower = question.lower()
        if re.search(r"\bclose\b.*(?:pr|pull\s*request)|(?:pr|pull\s*request).*\bclose\b|gh\s+pr\s+close", question_lower):
            context["operation_type"] = "close"
        elif re.search(r"force[\s-]?push|push\s+--force|push\s+-f\b|push\s+-[a-z]*f", question_lower):
            context["operation_type"] = "force-push"
        elif re.search(r"delete[\s-]?branch|branch\s+(?:-d|--delete)\b", question_lower):
            context["operation_type"] = "branch-delete"
        elif re.search(r"\bmerge\b", question_lower):
            context["operation_type"] = "merge"

    return context


def write_token(context: dict, token_dir: Path | None = None) -> str | None:
    """Write an authorization token file.

    Args:
        context: Operation context to include in the token
        token_dir: Override token directory (for testing)

    Returns:
        Path to the created token file, or None on failure or refusal
    """
    # Sparse-context guard: refuse to write a token whose context — as
    # produced by `extract_context()` on a vague AskUserQuestion text —
    # carries NONE of the three concrete anchor keys (pr_number, branch,
    # operation_type). The realistic shape of such a wildcard context is
    # `{question_snippet: "<vague text>"}` with no extracted anchors; a
    # token written from it would match ANY destructive command via the
    # PRE-side `_token_matches_command` ladder's ambiguous-permissive
    # fallback. Fail closed at the WRITE side so the wildcard token never
    # reaches the PRE-side ladder. Any one concrete anchor is sufficient.
    if not isinstance(context, dict):
        print(
            "[security] sparse context: non-dict context, refusing token write",
            file=sys.stderr,
        )
        return None
    has_pr = bool(context.get("pr_number"))
    has_branch = bool(context.get("branch"))
    has_op = bool(context.get("operation_type"))
    if not (has_pr or has_branch or has_op):
        print(
            "[security] sparse context: AskUserQuestion text yielded no "
            "extractable pr_number, branch, or operation_type — refusing "
            "token write to avoid wildcard-allow against subsequent "
            "destructive commands.",
            file=sys.stderr,
        )
        return None

    if token_dir is None:
        token_dir = TOKEN_DIR

    # Clean up stale .consumed token files from prior operations
    _cleanup_consumed_tokens(token_dir)

    now = time.time()
    timestamp = int(now)

    # Include session ID for cross-session scoping (graceful degradation)
    session_id = get_session_id()

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

        pact_context.init(input_data)
        tool_input = input_data.get("tool_input", {})
        # Defense-in-depth via SSOT helper: prefers canonical `tool_response`,
        # falls back to legacy `tool_output` for envelope-rename robustness,
        # warns on dual-envelope payloads (envelope-confusion smell).
        tool_response = extract_tool_response(input_data)

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
        # tool_response: {"answers": {"question_text": "answer_text"}, ...}
        answers = tool_response.get("answers", {})
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
