"""
Location: pact-plugin/hooks/shared/merge_guard_common.py
Summary: Shared constants and utilities for the merge guard hook pair.
Used by: merge_guard_pre.py (PreToolUse) and merge_guard_post.py (PostToolUse)

Centralizes TOKEN_TTL, TOKEN_DIR, TOKEN_PREFIX, consumed-token cleanup,
the regex-prefix constants (_GH_PREFIX, _GIT_PREFIX, etc.) and the
canonical destructive-command operation-type classifier
detect_command_operation_type. Both hooks call this classifier on the
SAME input when the prose-embed convention holds, guaranteeing
bidirectional write/read classification agreement (issue #720 Bug B).
"""

import glob
import os
import re
import time
from pathlib import Path

# Token TTL in seconds (5 minutes)
TOKEN_TTL = 300

# Directory for token files
TOKEN_DIR = Path.home() / ".claude"

# Token file prefix
TOKEN_PREFIX = "merge-authorized-"

# -----------------------------------------------------------------------------
# Regex prefix constants — shared between DANGEROUS_PATTERNS (read-side) and
# detect_command_operation_type (both sides). Centralized here so the
# write-side classifier can apply the SAME prefix semantics as the read-side
# pattern bank without duplicating regex source.
# -----------------------------------------------------------------------------

# Optional global flags between CLI tool and subcommand.
# (?:\S+\s+)* matches zero or more flag+value tokens (e.g., --repo owner/repo).
_GH_GLOBAL_FLAGS = r"(?:\S+\s+)*"  # broad — keep for DANGEROUS_PATTERNS (matches any token)
# Tight variant for PR-number extraction: only flag-shaped tokens
# (`-x`, `--long`, optionally `--flag value`). Prevents the capture group
# from greedily walking past the PR positional into heredoc body content,
# 2>&1 redirects, or trailing positional-digit tokens.
_GH_FLAG_TOKENS = r"(?:-\S*(?:\s+\S+)?\s+)*"
_GIT_GLOBAL_FLAGS = r"(?:\S+\s+)*"

# Composed prefixes for DRY usage across all patterns.
_GH_PREFIX = r"\bgh\s+" + _GH_GLOBAL_FLAGS
_GIT_PREFIX = r"\bgit\s+" + _GIT_GLOBAL_FLAGS
_GH_API_PREFIX = _GH_PREFIX + r"api\b"

# Pre-compiled patterns for the operation-type classifier (consistent with
# DANGEROUS_PATTERNS style).
_GH_PR_MERGE_RE = re.compile(_GH_PREFIX + r"pr\s+merge\b")
_GH_PR_CLOSE_RE = re.compile(_GH_PREFIX + r"pr\s+close\b")


def detect_command_operation_type(command: str) -> str | None:
    """Detect the operation type of a destructive command.

    Canonical classifier called from BOTH merge guard hooks. When the
    AskUserQuestion text (post-hook) embeds a literal command in a quoted
    region, the post-hook delegates to this function on the embedded
    command — guaranteeing the post-hook's operation_type tag matches
    what the pre-hook will compute for the same literal command, closing
    the asymmetric-classifier bug class (#720 Bug B).

    Returns:
        "merge"         - gh pr merge
        "close"         - gh pr close (any variant)
        "force-push"    - git push --force / git push -f (excludes --force-with-lease)
        "branch-delete" - git branch -D / git branch --delete --force / gh pr close --delete-branch
        None            - destructive shape not in the recognized set
                          (read-side caller treats None as "untyped command",
                          which the tightened token-match semantic treats as
                          a deny-on-typed-token signal rather than permissive)
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
    # lookahead matches the DANGEROUS_PATTERNS --force form.
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
    # same `(?!:)` refspec exclusion as DANGEROUS_PATTERNS push-to-main.
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
    # or git branch --force --delete (matches DANGEROUS_PATTERNS).
    if re.search(_GIT_PREFIX + r"branch\s+.*-D\b", command):
        return "branch-delete"
    if re.search(_GIT_PREFIX + r"branch\s+.*--delete\s+--force\b", command):
        return "branch-delete"
    if re.search(_GIT_PREFIX + r"branch\s+--force\s+--delete\b", command):
        return "branch-delete"
    return None


def cleanup_consumed_tokens(token_dir: Path) -> None:
    """Remove stale .consumed token files older than TOKEN_TTL.

    Called from both hooks: during token scanning (pre-hook) and during
    token creation (post-hook) to prevent accumulation.

    Args:
        token_dir: Directory containing token files
    """
    consumed_pattern = str(token_dir / f"{TOKEN_PREFIX}*.consumed")
    now = time.time()
    for consumed_path in glob.glob(consumed_pattern):
        try:
            # Use file modification time as a proxy for consumption time
            mtime = os.path.getmtime(consumed_path)
            if now - mtime > TOKEN_TTL:
                try:
                    os.unlink(consumed_path)
                except OSError:
                    pass
        except OSError:
            # File may have been cleaned up concurrently — ignore
            pass
