#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/merge_guard_pre.py
Summary: PreToolUse hook matching Bash — blocks dangerous git operations and
         API-based bypass attempts unless a valid authorization token exists.
Used by: hooks.json PreToolUse hook (matcher: Bash)

This hook is part of the merge guard system. It checks for a valid token
written by the companion hook (merge_guard_post.py) before allowing dangerous
operations. Detected operations include:
- CLI: git/gh merge, close with --delete-branch, force push, branch delete,
  push to main/master
- API: gh api, curl, wget, and httpie calls targeting merge, git/refs, or
  contents endpoints with mutating HTTP methods (including implicit POST via
  body parameter flags or data flags)

If no valid token exists, the command is blocked with a message directing the
user to confirm via AskUserQuestion first.

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

# Issue #658 / PR #660 Future #5: fail-closed wrapper around all module-level
# risky work (cross-package imports + regex compilations). If ANY of this load
# fails (broken Python install, missing shared.pact_context, syntax error in
# merge_guard_common, malformed regex), the harness sees a `permissionDecision:
# deny` output with the required `hookEventName` BEFORE the process exits —
# instead of an empty stdout that would fail open.
#
# The handler depends ONLY on stdlib modules already imported above this block
# (json, sys), so it remains functional even if every cross-package import below
# fails. Audit anchor: hookEventName must be present in any deny output.
try:
    import shared.pact_context as pact_context
    from shared.pact_context import get_session_id

    # Shared constants and cleanup — single source of truth for both hooks
    sys.path.insert(0, str(Path(__file__).parent))
    from shared.merge_guard_common import (
        TOKEN_TTL,
        TOKEN_DIR,
        TOKEN_PREFIX,
        cleanup_consumed_tokens as _cleanup_consumed_tokens,
    )
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    # Hand-built deny output using only stdlib (json, sys). Cannot rely on any
    # constants or helpers from the failed imports.
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Merge guard failed to load — blocking for safety. "
                "Check hook installation and shared module availability."
            ),
        }
    }))
    print(
        f"Hook load error (merge_guard_pre): {_module_load_error}",
        file=sys.stderr,
    )
    sys.exit(2)

# Optional global flags between CLI tool and subcommand.
# (?:\S+\s+)* matches zero or more flag+value tokens (e.g., --repo owner/repo).
_GH_GLOBAL_FLAGS = r"(?:\S+\s+)*"
_GIT_GLOBAL_FLAGS = r"(?:\S+\s+)*"

# Composed prefixes for DRY usage across all patterns.
_GH_PREFIX = r"\bgh\s+" + _GH_GLOBAL_FLAGS
_GIT_PREFIX = r"\bgit\s+" + _GIT_GLOBAL_FLAGS
_GH_API_PREFIX = _GH_PREFIX + r"api\b"

# Pre-serialized JSON for allow-path output: tells Claude Code UI to suppress
# the hook display instead of showing "hook error (No output)".
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _emit_load_failure_deny(stage: str, error: BaseException) -> None:
    """Emit fail-closed deny output for a module-load-time failure.

    Issue #658 / PR #660 Future #5: any module-level work that can fail
    (cross-package imports, regex compilations) must produce a structured
    deny — not an empty stdout that the harness treats as fail-open.

    Uses ONLY stdlib (json, sys) so it remains functional even when every
    cross-package import has failed. Audit anchor: hookEventName must be
    present in any deny output.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Merge guard failed to load — blocking for safety. "
                "Check hook installation and shared module availability."
            ),
        }
    }))
    print(f"Hook load error (merge_guard_pre, {stage}): {error}", file=sys.stderr)
    sys.exit(2)


# Patterns for dangerous commands
try:
    DANGEROUS_PATTERNS = [
    # PR merge via gh CLI
    re.compile(_GH_PREFIX + r"pr\s+merge\b"),
    # PR close with --delete-branch via gh CLI (bare close is reversible)
    re.compile(_GH_PREFIX + r"pr\s+close\b(?=.*--delete-branch)"),
    re.compile(r"--delete-branch.*" + _GH_PREFIX + r"pr\s+close\b"),
    # Force push (excludes --force-with-lease which is a safer alternative)
    re.compile(_GIT_PREFIX + r"push\s+.*--force(?!-with-lease)\b"),
    re.compile(_GIT_PREFIX + r"push\s+.*-f\b"),
    re.compile(_GIT_PREFIX + r"push\s+-[a-zA-Z]*f"),
    # Force branch deletion
    re.compile(_GIT_PREFIX + r"branch\s+.*-D\b"),
    re.compile(_GIT_PREFIX + r"branch\s+.*--delete\s+--force\b"),
    re.compile(_GIT_PREFIX + r"branch\s+--force\s+--delete\b"),
    # API-based merge bypasses (require mutating HTTP method to avoid blocking reads)
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X|--method)\s+(?:PUT|PATCH|POST)\b).*merge", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X|--request)\s+(?:PUT|PATCH|POST)\b).*api.*merge", re.IGNORECASE),
    # API-based branch deletion via DELETE to git/refs endpoint
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X|--method)\s+DELETE\b).*git/refs", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X|--request)\s+DELETE\b).*api.*git/refs", re.IGNORECASE),
    # API-based ref mutation / force push via mutating method to git/refs endpoint
    # (any mutating operation on git refs via API is inherently dangerous)
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X|--method)\s+(?:PATCH|POST|PUT)\b).*git/refs", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X|--request)\s+(?:PATCH|POST|PUT)\b).*api.*git/refs", re.IGNORECASE),
    # gh api implicit POST: body param flags (-f, -F, --field, --raw-field, --input)
    # cause gh api to default to POST. Dangerous when targeting git/refs or merge.
    # Negative lookahead excludes explicit GET (which overrides implicit POST).
    re.compile(_GH_API_PREFIX + r"(?!.*(?:-X|--method)\s+GET\b)(?=.*(?:-f|-F|--field|--raw-field|--input)\s).*git/refs", re.IGNORECASE),
    re.compile(_GH_API_PREFIX + r"(?!.*(?:-X|--method)\s+GET\b)(?=.*(?:-f|-F|--field|--raw-field|--input)\s).*merge", re.IGNORECASE),
    # curl implicit POST: --data/-d/--data-raw/--data-binary flags cause curl to
    # default to POST. Dangerous when targeting git/refs or merge API endpoints.
    # Negative lookahead excludes explicit GET (which overrides implicit POST).
    re.compile(r"\bcurl\b(?!.*(?:-X|--request)\s+GET\b)(?=.*(?:--data(?:-(?:raw|binary))?|-d)\s).*api.*git/refs", re.IGNORECASE),
    re.compile(r"\bcurl\b(?!.*(?:-X|--request)\s+GET\b)(?=.*(?:--data(?:-(?:raw|binary))?|-d)\s).*api.*merge", re.IGNORECASE),
    # Contents API: write operations (PUT/PATCH/POST) to /contents/ endpoint
    # targeting main or master branch. Flags any mutating /contents/ call that
    # mentions main or master anywhere in the command (acceptable false positive).
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X|--method)\s+(?:PUT|PATCH|POST)\b).*contents/.*(?:main|master)", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X|--request)\s+(?:PUT|PATCH|POST)\b).*api.*contents/.*(?:main|master)", re.IGNORECASE),
    # Alternative HTTP clients: wget with --method flag
    re.compile(r"\bwget\b(?=.*--method=(?:DELETE|PATCH|POST|PUT)\b).*git/refs", re.IGNORECASE),
    re.compile(r"\bwget\b(?=.*--method=(?:DELETE|PATCH|POST|PUT)\b).*merge", re.IGNORECASE),
    # Alternative HTTP clients: httpie (method is positional arg after 'http'/'https')
    # \bhttps?\s+ ensures word boundary + whitespace (won't match URLs like https://).
    # (?:\S+\s+)* allows optional flags (e.g., -a user:pass) between command and method.
    re.compile(r"\bhttps?\s+(?:\S+\s+)*(?:DELETE|PATCH|POST|PUT)\s.*git/refs", re.IGNORECASE),
    re.compile(r"\bhttps?\s+(?:\S+\s+)*(?:DELETE|PATCH|POST|PUT)\s.*merge", re.IGNORECASE),
    # Known API detection gaps (defense-in-depth, not a security boundary):
    # - GraphQL mutations: gh api graphql -f query='mutation { ... }' bypasses REST-path matching
    # - gh alias: aliases can hide API calls (tracked in #270)
    # Direct push to default branch (bypasses PR merge)
    re.compile(_GIT_PREFIX + r"push\s+\S+\s+HEAD:main\b"),
    re.compile(_GIT_PREFIX + r"push\s+\S+\s+HEAD:master\b"),
    # Regular push to main/master (e.g., local merge then push)
    # Negative lookahead (?!:) prevents matching refspecs like main:feature-branch
    re.compile(_GIT_PREFIX + r"push\s+(?:-\S+\s+)*\S+\s+main(?!:)\b"),
    re.compile(_GIT_PREFIX + r"push\s+(?:-\S+\s+)*\S+\s+master(?!:)\b"),
]

    # Pre-compiled patterns for helper functions (consistent with DANGEROUS_PATTERNS style).
    _GH_PR_MERGE_RE = re.compile(_GH_PREFIX + r"pr\s+merge\b")
    _GH_PR_CLOSE_RE = re.compile(_GH_PREFIX + r"pr\s+close\b")
    # PR number extraction: allows optional subcommand flags (e.g., --admin, --squash)
    # between merge/close and the PR number.
    _GH_PR_NUMBER_RE = re.compile(_GH_PREFIX + r"pr\s+(?:merge|close)\s+" + _GH_GLOBAL_FLAGS + r"(\d+)")
except BaseException as _pattern_compile_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("pattern compilation", _pattern_compile_error)


def _has_pipe_to_shell(command: str) -> bool:
    """Check if command pipes output to a shell interpreter.

    Detects patterns like ``echo "..." | bash``, ``printf "..." | sh``,
    and ``echo "..." | xargs bash`` where echo/printf content would be
    executed by the receiving shell.
    """
    return bool(
        re.search(r"\|\s*(?:bash|sh|zsh)\b", command)
        or re.search(r"\|\s*xargs\s+(?:.*\s+)?(?:bash|sh|zsh)\b", command)
    )


def _has_process_substitution_to_shell(command: str) -> bool:
    """Check if command uses process substitution fed to a shell interpreter.

    Detects patterns like ``bash <(echo "...")`` where the output of echo/printf
    inside ``<(...)`` is executed by the shell interpreter.
    """
    return bool(re.search(r"\b(?:bash|sh|zsh)\s+<\(", command))


def _has_eval_or_source(command: str) -> bool:
    """Check if command contains eval or source that could execute variable values.

    Detects patterns like ``CMD="..." && eval $CMD`` where a variable
    assignment value would be executed via eval or source.
    """
    return bool(re.search(r"\b(?:eval|source)\b", command))


def _var_is_expanded(var_name: str, command: str) -> bool:
    """Check if a variable is expanded (used) elsewhere in the command.

    Detects patterns like ``$VAR`` or ``${VAR}`` that would execute
    the variable's value as a command when used bare (e.g., ``CMD="gh pr merge 42" && $CMD``).
    """
    # Match $VAR (word boundary) or ${VAR}
    return bool(re.search(r"\$\{?" + re.escape(var_name) + r"\b", command))


def _has_command_substitution(quoted_content: str) -> bool:
    """Check if double-quoted content contains command substitution.

    ``$(...)`` and backticks inside double quotes are executed by the shell,
    so double-quoted strings containing them must not be stripped.
    Single-quoted strings never have substitution (handled separately).
    """
    return "$(" in quoted_content or "`" in quoted_content


def _strip_non_executable_content(command: str) -> str:
    """Strip shell content that is clearly non-executable before pattern matching.

    Removes text from contexts where dangerous-pattern text would not actually
    execute as a command: heredocs, comments, echo/printf arguments, and
    variable assignments. This prevents false positives without removing content
    from genuinely dangerous contexts like ``bash -c '...'``.

    Guards against execution-via-indirection: skips stripping when content
    would actually execute (piped to shell, eval'd, command substitution,
    heredoc fed to shell interpreter).

    Conservative: when in doubt, preserves text (false positive > missed threat).

    Args:
        command: The raw bash command string

    Returns:
        The command with non-executable content replaced by placeholders
    """
    result = command

    # 1. Strip heredoc bodies: << 'EOF' ... EOF, << EOF ... EOF, << "EOF" ... EOF
    #    Match the heredoc marker, then everything up to and including the
    #    closing marker on its own line.
    #    GUARD: Skip stripping if the heredoc is fed to a shell interpreter
    #    (e.g., bash << EOF ... EOF), because the body would execute.
    def _strip_heredoc(match: re.Match) -> str:
        # Check what command precedes the heredoc operator
        start = match.start()
        preceding = command[:start].rstrip()
        # If the preceding command is a shell interpreter, preserve content
        if re.search(r"\b(?:bash|sh|zsh)\s*$", preceding):
            return match.group(0)  # Preserve — content executes
        return "<<HEREDOC_STRIPPED"

    result = re.sub(
        r"<<-?\s*['\"]?(\w+)['\"]?.*?\n.*?\n\t*\1\b",
        _strip_heredoc,
        result,
        flags=re.DOTALL,
    )

    # 2. Strip comments: # to end of line
    #    Only strip when # appears at start of line or after whitespace/semicolon
    #    (not inside words like issue#42 or URLs with #fragment).
    result = re.sub(r"(?:^|(?<=\s)|(?<=;))\#.*$", "", result, flags=re.MULTILINE)

    # 3. Strip echo/printf quoted arguments
    #    Match echo/printf followed by flags then quoted strings.
    #    Replace the quoted content but keep the echo command visible.
    #    GUARD: Skip stripping if output is piped to a shell interpreter
    #    (including via xargs), or fed via process substitution to a shell,
    #    because the echo/printf content would be executed by the shell.
    #    NOTE: ``bash -c 'dangerous'`` is NOT affected by this stripping —
    #    the echo/printf regex only matches echo/printf commands, so
    #    ``bash -c`` content is implicitly preserved and correctly detected.
    piped_to_shell = _has_pipe_to_shell(command)
    process_sub_to_shell = _has_process_substitution_to_shell(command)
    if not piped_to_shell and not process_sub_to_shell:
        # Double-quoted: also guard against command substitution inside
        def _strip_echo_dq(match: re.Match) -> str:
            if _has_command_substitution(match.group(0)):
                return match.group(0)  # Preserve — $() executes
            return match.group(1) + " STRIPPED"

        result = re.sub(
            r'\b(echo|printf)\s+(?:-[neE]+\s+)*"(?:[^"\\]|\\.)*"',
            _strip_echo_dq,
            result,
        )
        result = re.sub(
            r"\b(echo|printf)\s+(?:-[neE]+\s+)*'[^']*'",
            r"\1 STRIPPED",
            result,
        )

    # 4. Strip variable assignment values: VAR="..." or VAR='...'
    #    Only match simple assignments (NAME=VALUE), not command arguments.
    #    GUARD: Skip stripping if eval/source appears in the command,
    #    because the variable value could be executed.
    #    GUARD: Skip stripping if $VAR or ${VAR} appears elsewhere in the
    #    command, because bare expansion executes the value as a command
    #    (e.g., CMD="gh pr merge 42" && $CMD).
    has_eval = _has_eval_or_source(command)
    if not has_eval:
        # Double-quoted: guard against command substitution and bare expansion
        def _strip_var_dq(match: re.Match) -> str:
            if _has_command_substitution(match.group(0)):
                return match.group(0)  # Preserve — $() executes
            var_name = match.group(1)
            if _var_is_expanded(var_name, command):
                return match.group(0)  # Preserve — $VAR executes
            return var_name + "=STRIPPED"

        result = re.sub(
            r'\b([A-Za-z_][A-Za-z0-9_]*)="(?:[^"\\]|\\.)*"',
            _strip_var_dq,
            result,
        )

        # Single-quoted: guard against bare expansion
        def _strip_var_sq(match: re.Match) -> str:
            var_name = match.group(1)
            if _var_is_expanded(var_name, command):
                return match.group(0)  # Preserve — $VAR executes
            return var_name + "=STRIPPED"

        result = re.sub(
            r"\b([A-Za-z_][A-Za-z0-9_]*)='[^']*'",
            _strip_var_sq,
            result,
        )

    # 5. Strip git commit -m quoted arguments
    #    The -m argument to git commit is a message, never executed.
    #    GUARD: Check for command substitution in double-quoted messages.
    def _strip_commit_msg_dq(match: re.Match) -> str:
        if _has_command_substitution(match.group(0)):
            return match.group(0)  # Preserve — $() executes
        return match.group(1) + ' -m STRIPPED'

    result = re.sub(
        r'\b(git\s+commit)\s+-m\s+"(?:[^"\\]|\\.)*"',
        _strip_commit_msg_dq,
        result,
    )
    result = re.sub(
        r"\b(git\s+commit)\s+-m\s+'[^']*'",
        r"\1 -m STRIPPED",
        result,
    )

    # 6. Strip here-string quoted arguments: <<< "..." or <<< '...'
    #    Here-strings pass text as stdin, not as a command.
    #    GUARD: Skip stripping if a shell interpreter precedes the <<<
    #    (e.g., bash <<< "dangerous"), because the content would execute.
    #    GUARD: Check for command substitution in double-quoted content.
    def _strip_herestring_dq(match: re.Match) -> str:
        # Check what command precedes the <<<
        start = match.start()
        preceding = command[:start].rstrip()
        if re.search(r"\b(?:bash|sh|zsh)\s*$", preceding):
            return match.group(0)  # Preserve — content executes
        if _has_command_substitution(match.group(0)):
            return match.group(0)  # Preserve — $() executes
        return "<<<STRIPPED"

    result = re.sub(
        r'<<<\s*"(?:[^"\\]|\\.)*"',
        _strip_herestring_dq,
        result,
    )

    def _strip_herestring_sq(match: re.Match) -> str:
        # Check what command precedes the <<<
        start = match.start()
        preceding = command[:start].rstrip()
        if re.search(r"\b(?:bash|sh|zsh)\s*$", preceding):
            return match.group(0)  # Preserve — content executes
        return "<<<STRIPPED"

    result = re.sub(
        r"<<<\s*'[^']*'",
        _strip_herestring_sq,
        result,
    )

    return result


def is_dangerous_command(command: str) -> bool:
    """Check if a bash command is a dangerous git operation.

    Strips non-executable content (heredocs, comments, echo arguments, variable
    assignments) before matching, to avoid false positives when dangerous-pattern
    text appears in non-command contexts.

    Args:
        command: The bash command string

    Returns:
        True if the command matches a dangerous pattern
    """
    # Normalize bash line continuations (\<newline>) before any matching.
    # Without this, patterns split across lines bypass all regex detection.
    command = command.replace("\\\n", " ")
    stripped = _strip_non_executable_content(command)
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(stripped):
            return True
    return False


def find_valid_token(token_dir: Path | None = None) -> tuple[dict, str] | tuple[None, None]:
    """Find a valid (unexpired) authorization token for the current session.

    Also cleans up any expired token files. If a session ID is available
    (via pact_context), only tokens from the current session are accepted.
    If not available, any valid token is accepted (graceful degradation).

    Args:
        token_dir: Override token directory (for testing)

    Returns:
        Tuple of (token_data, token_path) if a valid token exists,
        (None, None) otherwise
    """
    if token_dir is None:
        token_dir = TOKEN_DIR

    current_session = get_session_id()

    now = time.time()
    valid_token = None
    valid_path = None
    token_pattern = str(token_dir / f"{TOKEN_PREFIX}*")

    for token_path in glob.glob(token_pattern):
        # Skip consumed tokens — they were already used by a prior invocation
        if token_path.endswith(".consumed"):
            continue

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

    # Clean up stale consumed tokens while we're scanning
    _cleanup_consumed_tokens(token_dir)

    return valid_token, valid_path


def _safe_remove(path: str):
    """Remove a file, ignoring errors if it doesn't exist."""
    try:
        os.unlink(path)
    except OSError:
        pass


def _consume_token(token_path: str) -> bool:
    """Consume a token by renaming it to .consumed (idempotent).

    Uses os.rename() for POSIX atomicity. If the rename fails because the
    file was already renamed by a concurrent invocation, that IS the success
    case — the token was already consumed for this operation.

    The fallback verification uses an atomic open() instead of os.path.exists()
    to avoid a TOCTOU race where the .consumed file could be deleted between
    the existence check and any subsequent read.

    Args:
        token_path: Path to the token file to consume

    Returns:
        True if the token was consumed (either by us or by a prior invocation),
        False if consumption failed for an unexpected reason
    """
    consumed_path = token_path + ".consumed"
    try:
        os.rename(token_path, consumed_path)
        return True
    except FileNotFoundError:
        # Token already renamed by a concurrent invocation — verify the
        # .consumed file exists atomically by trying to open it. This avoids
        # a TOCTOU race with os.path.exists() where the file could vanish
        # between the check and any subsequent use.
        try:
            fd = os.open(consumed_path, os.O_RDONLY)
            os.close(fd)
            return True
        except FileNotFoundError:
            # Neither original nor .consumed exists — token was genuinely lost
            return False
        except OSError:
            # Unexpected error accessing .consumed — fail closed
            return False
    except OSError:
        # Unexpected error — fail closed (return False to block)
        return False


def _detect_command_operation_type(command: str) -> str | None:
    """Detect the operation type of a dangerous command.

    Returns:
        "merge" for gh pr merge, "close" for gh pr close (any variant),
        or None for other operation types (force push, branch delete, etc.)
    """
    if _GH_PR_MERGE_RE.search(command):
        return "merge"
    if _GH_PR_CLOSE_RE.search(command):
        return "close"
    return None


def _token_matches_command(token: dict, command: str) -> bool:
    """Check if a token's context is consistent with the command being executed.

    If the token has specific context (PR number, branch name, operation type),
    verify the command matches. If parsing is ambiguous or no context is
    available, allow through to avoid false negatives.

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
    token_op_type = context.get("operation_type")

    # Check operation type: a merge token should not authorize a close and
    # vice versa. If either side has no operation_type, skip the check
    # (ambiguous is permissive for backward compatibility with old tokens).
    if token_op_type:
        cmd_op_type = _detect_command_operation_type(command)
        if cmd_op_type and token_op_type != cmd_op_type:
            return False

    # If token has a PR number, check gh pr merge/close commands match
    if pr_number:
        pr_match = _GH_PR_NUMBER_RE.search(command)
        if pr_match:
            return pr_match.group(1) == str(pr_number)

    # If token has a branch, check branch deletion commands match
    if branch:
        branch_d_match = re.search(_GIT_PREFIX + r"branch\s+.*-D\s+(\S+)", command)
        if branch_d_match:
            return branch_d_match.group(1) == branch
        branch_delete_match = re.search(
            _GIT_PREFIX + r"branch\s+.*--delete\s+(?:--force\s+)?(\S+)", command
        )
        if branch_delete_match:
            return branch_delete_match.group(1) == branch

    # No specific context to validate against, or command type doesn't match
    # context type — allow through (ambiguous is permissive)
    return True


def check_merge_authorization(command: str, token_dir: Path | None = None) -> str | None:
    """Check if a dangerous command is authorized.

    Tokens are single-use: once a token authorizes a command, it is consumed
    (renamed to .consumed) so that each approval authorizes exactly one operation.
    The rename is atomic on POSIX filesystems and idempotent — if a concurrent
    hook invocation already consumed the token, the second invocation recognizes
    the .consumed file and allows the command. The token's context is validated
    against the command to ensure the approved operation matches what is being
    executed.

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
            # Uses rename for idempotent consumption: if a concurrent
            # invocation already consumed it, that's the success case.
            if _consume_token(token_path):
                return None
            # Consumption failed for unexpected reason — fail closed
            return (
                "Merge guard internal error — could not consume authorization token. "
                "Use AskUserQuestion to get approval again."
            )
        else:
            # Token exists but doesn't match this command — don't consume it,
            # block the mismatched command
            return (
                "Authorization token exists but does not match this operation. "
                "Use AskUserQuestion to get approval for this specific operation."
            )

    return (
        "Merge/close/force-push/branch-delete requires user approval via AskUserQuestion. "
        "Use AskUserQuestion to confirm with the user before proceeding."
    )


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        pact_context.init(input_data)
        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        if not command:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        error = check_merge_authorization(command)

        if error:
            # hookEventName is required by the harness; missing it silently fails open
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": error,
                }
            }
            print(json.dumps(output))
            sys.exit(2)

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        # Security guard fails closed — block on unexpected errors
        print(f"Hook error (merge_guard_pre): {e}", file=sys.stderr)
        try:
            # hookEventName is required by the harness; missing it silently fails open
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
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
