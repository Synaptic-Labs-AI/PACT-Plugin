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

Token-lifecycle invariants (pinned by tests/test_merge_guard.py class
TestTokenLifecycleInvariants):

  I-1 (at most one unused token at any time):
      cleanup_unused_tokens() is called from write_token() BEFORE
      os.open(O_EXCL). Any prior unused token is atomically renamed to
      .consumed before the new one exists on disk.

  I-2 (successful operation immediately retires the token):
      merge_guard_post.main() Bash branch detects successful
      `gh pr merge` (dict-shape tool_response + interrupted=false +
      op_type=merge + "Merged pull request" in stdout) and atomically
      renames the consuming token to .consumed regardless of MAX_USES.

  I-3 (TTL expiry retires the token):
      merge_guard_pre.find_valid_token enforces `expires_at < now` and
      removes expired tokens via _safe_remove. Audit-only invariant in
      this module (no helper here; pinned by alias test).

  I-4 (failed operation preserves token for retry within TTL up to MAX_USES):
      merge_guard_pre._consume_token N-use slot semantics. .use-N
      markers atomically claim slots via O_EXCL; final slot triggers
      terminal .consumed rename. Audit-only invariant in this module.

  I-5 (cross-session tokens never valid):
      merge_guard_pre.find_valid_token enforces
      current_session == token_session when both are present. Audit-
      only invariant in this module.

Cross-cutting cleanup: cleanup_orphan_tokens() reaps unconsumed tokens
whose mtime exceeds ORPHAN_TOKEN_MAX_AGE_SECONDS (12x TOKEN_TTL).
Triggered from merge_guard_pre.find_valid_token (primary, load-bearing
on every dangerous-Bash precheck) and session_init.main (secondary,
eager-cleanup at session start). Disk-hygiene defense — not a primary
security check; the primary check is I-3 TTL expiry at 5 minutes.
"""

from __future__ import annotations

import glob
import os
import re
import time
from pathlib import Path

from .paths import get_claude_config_dir

# Token TTL in seconds (5 minutes)
TOKEN_TTL = 300

# Directory for token files. B2 (import-time binding): CLAUDE_CONFIG_DIR is
# fixed per-process before this module is imported, so an eager SSOT-derived
# read is production-correct here; the merge-guard tests patch THIS attribute
# (not the env), so they stay valid and non-vacuous. Derive from the SSOT
# resolver eagerly — do NOT re-hardcode Path.home()/".claude" (that breaks the
# single-source-of-truth). Convert to a call-time accessor only if call-time
# env-following is ever needed (TOKEN_DIR's write+read are both PACT-side, so it
# never needs to follow a post-import env change).
TOKEN_DIR = get_claude_config_dir()

# Token file prefix
TOKEN_PREFIX = "merge-authorized-"

# Default max-use budget per authorization token. A token can authorize up
# to MAX_USES identical-context retries within TOKEN_TTL before requiring
# fresh AskUserQuestion approval. Set to 2 — the smallest N that resolves
# the empirical retry-on-transient-failure case (single retry of an
# identical command) without further eroding per-use-confirmation
# discipline. A third identical retry still re-prompts via
# AskUserQuestion, preserving the "stop and reconsider" checkpoint.
# Audit: tightening this value is always safe (more re-prompting);
# loosening (N>2) requires empirical justification — there is no current
# case that needs >2 same-context retries.
MAX_USES = 2

# Suffix used by per-use marker files. Each marker file is created via
# O_EXCL to atomically claim one use slot of an N-use token (#720 Bug C).
USE_MARKER_SUFFIX = ".use-"

# Orphan-token cleanup threshold. Tokens that survive past this window without
# being consumed or used are reaped as disk hygiene — they cannot be legitimate
# (TOKEN_TTL=300s already expires them for authorization). 12x TOKEN_TTL gives
# strong margin against any legitimate in-flight token while aggressively
# bounding accumulation. Disk-hygiene defense — not a primary security check;
# the primary check is TOKEN_TTL expiry (invariant I-3).
ORPHAN_TOKEN_MAX_AGE_SECONDS = 3600

# Layer 1 Block 3 (gh CLI / git semantic signal) per op_type — SEC-S2 cycle-2.
# Each value is a substring that MUST appear in tool_response.stdout for the
# op_type's successful invocation to retire the consuming token. A value of
# None means "skip Block 3 for this op_type": the 3-block predicate degrades
# to 2 blocks (Block 1 op_type match + Block 2 platform success signal).
# force-push uses None because git push --force emits primarily to STDERR;
# the empty-STDOUT case is fail-closed-on-no-signal (no retirement degrades
# to TTL/MAX_USES safety net). New op_types: add 1 entry + tests; no other
# changes required (lookup table is the SSOT, mirrors DANGEROUS_PATTERNS
# convention).
LAYER1_SUCCESS_STDOUT_PATTERNS: dict[str, str | None] = {
    "merge": "Merged pull request",
    "close": "Closed pull request",
    "branch-delete": "Deleted branch",
    "force-push": None,
}

# -----------------------------------------------------------------------------
# Regex prefix constants — shared between DANGEROUS_PATTERNS (read-side) and
# detect_command_operation_type (both sides). Centralized here so the
# write-side classifier can apply the SAME prefix semantics as the read-side
# pattern bank without duplicating regex source.
# -----------------------------------------------------------------------------

# Upper bound on flag tokens in a CLI flag region. Governs BOTH the global-flag
# prefix between a tool and its subcommand (e.g. `git -c k=v ... push`) AND the
# push-dash-flag walk between `push` and its refspec (e.g. `git push -u -f main`).
# The global-flag prefix bound eliminates the O(n^2) multi-anchor backtracking of
# the unbounded `*` form (#1001); the push-dash-flag walk bound is defense-in-
# depth structural-linearity (that walk was already linear once the prefix is
# bounded — bounding the inner walk makes its linearity intrinsic rather than
# contingent). Both preserve the "matches any token" semantics EXACTLY for any
# command with <= _MAX_GLOBAL_FLAG_TOKENS flag tokens in that region — i.e. every
# realistic command (the heaviest realistic git global-flag count, e.g.
# `git -c a=1 -c b=2 -C /p --git-dir=/g --work-tree=/w push ...`, is ~10 tokens;
# gh is ~2; push dash-flags ~2-3). 32 is ~3x that headroom, and is a fixed modest
# constant so per-anchor work is O(32)=O(1) regardless of input length.
#
# ACCEPTED RESIDUAL (honest INV-D2 accounting): a command with >32 *valid* flag
# tokens before its verb/refspec is NOT impossible — `git -c k=v` is a
# legitimate, repeatable pair, so e.g. `git -c a=1 -c b=2 ...(17 pairs=34
# tokens)... push --force` DOES execute yet exceeds the bound, so the bounded
# form misses a real destructive op the unbounded form caught. This is a
# NARROW residual under-block, accepted as a documented tradeoff against the
# O(n^2) DoS, justified by the THREAT MODEL: #1001's input is operator/LLM-
# authored command text (defense-in-depth, NOT adversarial network input), and
# padding 17+ `-c` pairs to evade one's OWN merge guard is self-defeating (the
# author would simply write the command directly). The push-dash-flag walk
# carries the SAME residual class but is even less reachable (push dash-flags are
# not meaningfully infinitely-repeatable; a flag with a non-dash value, e.g.
# `-o <opt>`, already breaks the walk). It is a relaxation of INV-D2, not a
# no-op — stated plainly rather than papered over.
# DO NOT raise this constant casually: a larger cap scales the per-anchor work,
# and on a pathological multi-anchor input the constant factor grows measurably
# (a larger value carries a real, if modest, cost — it is not free). Keep it a
# small fixed value so per-anchor work stays O(1)/linear.
_MAX_GLOBAL_FLAG_TOKENS = 32

# Optional global flags between CLI tool and subcommand — BOUNDED (was `*`).
_GH_GLOBAL_FLAGS  = r"(?:\S+\s+){0,%d}" % _MAX_GLOBAL_FLAG_TOKENS
# Tight variant for PR-number extraction — UNCHANGED (already linear; requires
# a leading `-` per token so it fails fast; used only by _GH_PR_NUMBER_RE).
_GH_FLAG_TOKENS   = r"(?:-\S*(?:\s+\S+)?\s+)*"
_GIT_GLOBAL_FLAGS = r"(?:\S+\s+){0,%d}" % _MAX_GLOBAL_FLAG_TOKENS

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
        _GIT_PREFIX + r"push\s+(?:-(?!-force-with-lease\b)\S+\s+){0,%d}\S+\s+(?:main|master)(?!:)\b" % _MAX_GLOBAL_FLAG_TOKENS,
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
    """Remove stale .consumed token files and .use-N markers older than TOKEN_TTL.

    Called from both hooks: during token scanning (pre-hook) and during
    token creation (post-hook) to prevent accumulation. The .use-N markers
    accompany N-use tokens (#720 Bug C) and persist on disk alongside the
    .consumed terminal-rename until the TTL window elapses.

    Args:
        token_dir: Directory containing token files
    """
    now = time.time()
    patterns = (
        str(token_dir / f"{TOKEN_PREFIX}*.consumed"),
        str(token_dir / f"{TOKEN_PREFIX}*{USE_MARKER_SUFFIX}*"),
    )
    for pattern in patterns:
        for stale_path in glob.glob(pattern):
            try:
                # Use file modification time as a proxy for consumption time
                mtime = os.path.getmtime(stale_path)
                if now - mtime > TOKEN_TTL:
                    try:
                        os.unlink(stale_path)
                    except OSError:
                        pass
            except OSError:
                # File may have been cleaned up concurrently — ignore
                pass


def cleanup_unused_tokens(token_dir: Path) -> None:
    """Atomically retire (rename to .consumed) any unused tokens in token_dir.

    Maintains invariant I-1 (at most one unused token at any time). Called
    from merge_guard_post.write_token() BEFORE the O_EXCL create of the new
    token so that, at the instant O_EXCL succeeds, the directory holds zero
    unused tokens (just cleaned) plus the new one — exactly one.

    Concurrency model: POSIX rename(2) is atomic on the same filesystem.
    When two writers race, exactly one rename of any given source path
    succeeds; the loser raises FileNotFoundError which is swallowed. No
    fs-lock required.

    Args:
        token_dir: Directory containing token files

    Side effects:
        Renames matching unused token files to <path>.consumed. Skips
        already-.consumed paths and .use-N marker siblings (the latter
        are auxiliary files for N-use slot claims; reaped by
        cleanup_consumed_tokens at TOKEN_TTL boundary).
    """
    pattern = str(token_dir / f"{TOKEN_PREFIX}*")
    for path in glob.glob(pattern):
        # Already terminal — skip to avoid creating .consumed.consumed shape.
        if path.endswith(".consumed"):
            continue
        # Per-use slot markers are NOT tokens; preserve them as audit trail
        # alongside their parent token's retirement (cleanup_consumed_tokens
        # reaps them at the TOKEN_TTL boundary).
        if USE_MARKER_SUFFIX in os.path.basename(path):
            continue
        try:
            os.rename(path, path + ".consumed")
        except (FileNotFoundError, OSError):
            # Concurrent retire (another writer's cleanup, or _consume_token
            # claiming the same path) won the race — the invariant holds
            # either way. Swallow.
            pass


def cleanup_orphan_tokens(
    token_dir: Path,
    max_age_seconds: int = ORPHAN_TOKEN_MAX_AGE_SECONDS,
) -> None:
    """Reap unconsumed tokens older than max_age_seconds (disk hygiene).

    Targets tokens that escaped the normal lifecycle — e.g., when the
    consuming dangerous-Bash command was never executed after authorization,
    leaving a token to expire silently. TOKEN_TTL (300s) already expires
    them for authorization purposes; this helper unlinks them from disk to
    bound accumulation.

    Idempotent. Fail-open on all OSError paths (file gone, permission
    denied, dir missing) — disk hygiene must never block any caller.

    Args:
        token_dir: Directory containing token files
        max_age_seconds: Reap threshold (default ORPHAN_TOKEN_MAX_AGE_SECONDS).

    Side effects:
        Unlinks matching token files. Skips .consumed and .use-N markers.
    """
    now = time.time()
    pattern = str(token_dir / f"{TOKEN_PREFIX}*")
    for path in glob.glob(pattern):
        if path.endswith(".consumed"):
            continue
        if USE_MARKER_SUFFIX in os.path.basename(path):
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            # File gone between glob and stat — race-safe no-op.
            continue
        if now - mtime > max_age_seconds:
            try:
                os.unlink(path)
            except OSError:
                # Race vs concurrent cleanup or permission flake — swallow.
                pass
