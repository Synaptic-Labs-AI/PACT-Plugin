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
_GH_GLOBAL_FLAGS = r"(?:\S+\s+)*"  # broad — keep for DANGEROUS_PATTERNS (matches any token)
# Tight variant for PR-number extraction (#665): only flag-shaped tokens
# (`-x`, `--long`, optionally `--flag value`). Prevents the capture group
# from greedily walking past the PR positional into heredoc body content,
# 2>&1 redirects, or trailing positional-digit tokens.
_GH_FLAG_TOKENS = r"(?:-\S*(?:\s+\S+)?\s+)*"
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
    #
    # Both flag-walks (between `gh` and `pr`, AND between subcommand and PR
    # number) use the tight `_GH_FLAG_TOKENS` form. The earlier broad
    # `_GH_GLOBAL_FLAGS` form on the pre-subcommand walk allowed greedy
    # consumption past a `gh pr <subcmd> <PR>` substring inside `--body
    # "..."` text, then re-anchoring at a SECOND `gh pr <subcmd>` occurrence
    # embedded in the body. That re-anchor permitted an authorization-bypass
    # attack where the body text contained `gh pr merge <fake_PR>` and the
    # token-context check matched against the embedded fake PR number rather
    # than the real positional. Restricting both walks to flag-shaped tokens
    # only prevents the engine from walking past the real positional into
    # quoted body content.
    #
    # The trailing `(?![\w-])` rejects BOTH alphanumeric-suffix tokens
    # (e.g., `7352abc`) AND hyphen-suffix tokens (e.g., `7352-tests`).
    # Python `\b` is a word-boundary that DOES match at digit-to-hyphen
    # (because `-` is a non-word character), so a plain `\b` would
    # incorrectly capture `7352` from `7352-tests` (a branch-name argument
    # to `gh pr merge`). The negative-lookahead form `(?![\w-])` is
    # strictly stronger: it rejects any continuation that is a word char
    # OR a hyphen, which closes the branch-name suffix-match case while
    # preserving rejection of the alphanumeric-suffix case.
    _GH_PR_NUMBER_RE = re.compile(
        r"\bgh\s+" + _GH_FLAG_TOKENS + r"pr\s+(?:merge|close)\s+"
        + _GH_FLAG_TOKENS + r"(\d+)(?![\w-])"
    )
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


def _has_eval_with_heredoc(command: str) -> bool:
    """Detect eval (or backtick) command-substitution that wraps a heredoc.

    The strip pipeline removes heredoc bodies BEFORE the regex-match phase.
    An eval-wrapped destructive command inside a heredoc body is therefore
    invisible to DANGEROUS_PATTERNS by the time matching runs:

        eval $(cat <<HEREDOC
        gh pr merge 999 --admin
        HEREDOC
        )

    After ``_strip_non_executable_content``, the inner ``gh pr merge 999``
    is gone. The outer eval invokes the heredoc body as a command, which
    is exactly the destructive operation the merge guard is supposed to
    intercept. Treat the eval+heredoc shape as categorically dangerous —
    legitimate operator command flows do not use eval-wrapped heredoc as
    a delivery mechanism, so the false-positive risk is low.

    Detects both the modern ``$(...)`` substitution form and the legacy
    backtick form.
    """
    # eval $(...) with a heredoc anywhere within the substitution
    if re.search(r"\beval\s+\$\(", command) and "<<" in command:
        return True
    # eval `...` (backtick) wrapping a heredoc
    if re.search(r"\beval\s+`[^`]*<<", command):
        return True
    return False


# Compound-command detection: shell control-flow operators that split a
# command line into multiple independent operations. When a destructive
# operation appears inside a compound, the merge-guard token model breaks
# down — a single AskUserQuestion approval is presumed by the operator to
# authorize ONE operation, but the compound runs many.
_COMPOUND_OPS_RE = re.compile(r"[&;|\n]")


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
    # Pre-strip detection: eval+heredoc shape obscures destructive ops via
    # the heredoc-strip pipeline. Treat as dangerous before the strip runs.
    if _has_eval_with_heredoc(command):
        return True

    # Normalize bash line continuations (\<newline>) before any matching.
    # Without this, patterns split across lines bypass all regex detection.
    command = command.replace("\\\n", " ")
    stripped = _strip_non_executable_content(command)
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(stripped):
            return True
    return False


def is_compound_destructive_command(command: str) -> bool:
    """Detect destructive operations chained inside a shell compound shape.

    Returns True iff the stripped command contains BOTH (a) a compound-shape
    character (``&&``, ``||``, ``;``, ``|``, newline) AND (b) a
    DANGEROUS_PATTERNS match. Safe compounds (``ls && pwd``) are NOT flagged.

    Operator-side review of AskUserQuestion text typically focuses on the
    headline command and may miss a chained second destructive op, e.g.:

        gh pr merge 100 && gh pr merge 999 --admin

    Single-token authorization for the headline command would otherwise
    let the second op execute unauthorized. Reject compound destructive
    shapes outright; force one-op-at-a-time with one checkpoint each.
    """
    normalized = command.replace("\\\n", " ")
    stripped = _strip_non_executable_content(normalized)
    if not _COMPOUND_OPS_RE.search(stripped):
        return False
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

    Symmetric with `extract_context()` in merge_guard_post.py: both must
    recognize the SAME four operation classes so write-side tokens and
    read-side commands compare on a common axis.

    Returns:
        "merge"         — gh pr merge
        "close"         — gh pr close (any variant)
        "force-push"    — git push --force / git push -f (excludes --force-with-lease)
        "branch-delete" — git branch -D / git branch --delete --force / gh pr close --delete-branch
        None            — destructive shape not in the recognized set
                          (caller treats None as "untyped command", which the
                          tightened token-match semantic now treats as a
                          deny-on-typed-token signal rather than permissive)
    """
    # Order matters: gh pr close --delete-branch is BOTH a close and a
    # branch-delete operation; the AskUserQuestion-side classifier
    # (extract_context) tags it as "close" in priority order, so match
    # the same precedence here for write/read symmetry.
    if _GH_PR_MERGE_RE.search(command):
        return "merge"
    if _GH_PR_CLOSE_RE.search(command):
        # gh pr close --delete-branch is a close-type operation per the
        # write-side classifier. Branch-delete-via-pr-close is folded into
        # the close class on both sides for symmetric authorization.
        return "close"
    # force-push: git push ... --force (excludes --force-with-lease which
    # the existing DANGEROUS_PATTERNS treats as safe). The negative
    # lookahead matches the DANGEROUS_PATTERNS L127 form.
    if re.search(_GIT_PREFIX + r"push\s+.*--force(?!-with-lease)\b", command):
        return "force-push"
    if re.search(_GIT_PREFIX + r"push\s+.*-f\b", command):
        return "force-push"
    if re.search(_GIT_PREFIX + r"push\s+-[a-zA-Z]*f", command):
        return "force-push"
    # Direct push to default branch is force-push-class (bypasses PR
    # review). Match the existing DANGEROUS_PATTERNS forms but require
    # the dangerous shape to actually fire — the negative-lookahead-free
    # pattern `git push X main` would over-match safer flows. Use the
    # same `(?!:)` refspec exclusion as DANGEROUS_PATTERNS L175-176.
    if re.search(_GIT_PREFIX + r"push\s+\S+\s+HEAD:(?:main|master)\b", command):
        return "force-push"
    if re.search(
        _GIT_PREFIX + r"push\s+(?:-(?!-force-with-lease\b)\S+\s+)*\S+\s+(?:main|master)(?!:)\b",
        command,
    ):
        return "force-push"
    # API-based ref-mutation forms (gh api / curl / wget targeting
    # /git/refs with mutating HTTP methods) classify by HTTP semantic:
    # DELETE → branch-delete class (removes a ref)
    # PATCH/POST/PUT → force-push class (rewrites a ref without PR review)
    # Symmetric with how a force-push or branch-delete token from
    # extract_context() would authorize the equivalent CLI form.
    _is_api_form = re.search(r"\b(?:gh\s+api|curl|wget)\b", command, re.IGNORECASE)
    if _is_api_form and "git/refs" in command:
        if re.search(r"\bDELETE\b", command):
            return "branch-delete"
        if re.search(r"\b(?:PATCH|POST|PUT)\b", command):
            return "force-push"
    # branch-delete: git branch -D, git branch --delete --force,
    # or git branch --force --delete (matches DANGEROUS_PATTERNS L131-133).
    if re.search(_GIT_PREFIX + r"branch\s+.*-D\b", command):
        return "branch-delete"
    if re.search(_GIT_PREFIX + r"branch\s+.*--delete\s+--force\b", command):
        return "branch-delete"
    if re.search(_GIT_PREFIX + r"branch\s+--force\s+--delete\b", command):
        return "branch-delete"
    return None


# Allowlist of `gh pr merge|close` long-form flags KNOWN to take a value.
# The F3 defensive check only rejects digits preceded by one of these
# value-taking flags (avoiding false-positives on value-less flags like
# `--admin`, `--auto`, `--squash` whose positional digit IS the PR).
#
# As of `gh` v2 (2026-04 baseline), no real `gh pr merge|close` flag
# takes a digit value; this allowlist is a forward-compatible defense
# for hypothetical future flags. `--max-retries` is the canonical
# example cited in the F3 review (test-engineer-2). Extend this list
# when `gh` ships a flag that takes a numeric value.
_GH_PR_VALUE_TAKING_FLAGS = frozenset({
    # Known string/path-value flags from `gh pr merge --help` /
    # `gh pr close --help`. Listed here for forward-compat: if any of
    # these were given a digit value (e.g. `--subject 123`), the
    # defensive check correctly rejects the digit-as-flag-value capture.
    "--body",
    "--body-file",
    "--subject",
    "--author-email",
    "--match-head-commit",
    "--comment",
    # Hypothetical future flags. Add here when gh ships one.
    "--max-retries",
    "--retry-count",
    "--timeout",
})


def _extract_pr_number(command: str) -> str | None:
    """Extract the PR number positional from a `gh pr merge|close` command.

    Wraps `_GH_PR_NUMBER_RE.search()` with a defensive post-extract check
    that rejects digits which are actually the VALUE of an immediately-
    preceding value-taking long-form flag (e.g., `--max-retries 5`).

    The defensive check is narrowly scoped to flags in
    `_GH_PR_VALUE_TAKING_FLAGS`. Value-less flags like `--admin`,
    `--auto`, `--squash` do NOT trigger the check — a digit immediately
    after one of them IS the PR positional. This avoids the false-
    negative class where a real PR positional after a value-less flag
    would be incorrectly rejected.

    No current `gh pr merge|close` flag takes a digit value, so the
    realistic risk is theoretical — but this is defense-in-depth post
    the cycle-3 strict-match enforcement: a typed token would otherwise
    compare against the wrong digit and emit a confusing
    "does-not-match" deny. Returning None here lets the comparison fall
    through to the ambiguous-permissive path on the pr_number axis
    (op_type strict-match still applies).
    """
    match = _GH_PR_NUMBER_RE.search(command)
    if not match:
        return None
    pr_pos = match.start(1)
    # Inspect the immediately-preceding token for a known value-taking
    # long-form flag. Match captures the flag name (without trailing
    # whitespace) so we can look it up in the allowlist.
    preceding = command[:pr_pos].rstrip()
    flag_match = re.search(r"(--[\w-]+)$", preceding)
    if flag_match and flag_match.group(1) in _GH_PR_VALUE_TAKING_FLAGS:
        return None
    return match.group(1)


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

    # Cross-operation authorization guard: a typed token (one with a known
    # operation_type) MUST match the command's detected operation type, OR
    # the command shape is unrecognized. Symmetric coverage —
    # `_detect_command_operation_type` recognizes all four classes
    # (merge / close / force-push / branch-delete) so a missing cmd_op_type
    # means the destructive shape is outside the recognized set, not a
    # legitimate "untyped" path. Refuse the cross-op authorization rather
    # than fall through permissively (the prior fall-through let
    # token{op=merge} authorize `git push --force` because cmd_op_type was
    # None — closed by extending the detector + tightening this check).
    #
    # Untyped tokens (no operation_type — only shipped pre-cycle-2; should
    # not occur post-#700 sparse-context guard at write time) still fall
    # through to the pr_number/branch checks for backward compatibility.
    if token_op_type:
        cmd_op_type = _detect_command_operation_type(command)
        if cmd_op_type is None or token_op_type != cmd_op_type:
            return False

    # If token has a PR number, check gh pr merge/close commands match.
    # Use _extract_pr_number for the defensive long-flag-value check so a
    # token's pr_number is not compared against a flag-value digit
    # (e.g., the `5` in `gh pr merge --max-retries 5 --auto`).
    if pr_number:
        cmd_pr = _extract_pr_number(command)
        if cmd_pr is not None:
            return cmd_pr == str(pr_number)

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

    # Compound destructive shapes are categorically denied — a single
    # token cannot authorize multiple chained ops. The operator must run
    # one destructive op per checkpoint. Checked BEFORE the token lookup
    # so a valid token for the headline op cannot accidentally authorize
    # the chained second op.
    if is_compound_destructive_command(command):
        return (
            "Compound destructive command rejected — `&&`, `||`, `;`, `|`, and "
            "newlines cannot be authorized atomically. A single AskUserQuestion "
            "approval can only authorize ONE destructive operation. Run each "
            "destructive op separately with its own approval."
        )

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
