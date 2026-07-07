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
  push to main/master, remote ref delete / mass delete (push :ref, --delete,
  --mirror, --prune)
- API: gh api, curl, and wget calls targeting merge, git/refs, contents, or
  branch-protection endpoints with mutating HTTP methods (including implicit
  POST via body parameter flags or data flags; protection mutations gate on
  the weakening methods DELETE|PUT|PATCH — POST is excluded as strengthening)

If no valid token exists, the command is blocked with a message directing the
user to confirm via AskUserQuestion first.

Input: JSON from stdin with tool_input containing the command
Output: JSON with hookSpecificOutput.permissionDecision if blocking
"""

from __future__ import annotations

import glob
import json
import os
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
    # Names this hook uses directly.
    from shared.merge_guard_common import (
        is_dangerous_command,
        is_compound_destructive_command,
        TOKEN_DIR,
        TOKEN_PREFIX,
        USE_MARKER_SUFFIX,
        cleanup_consumed_tokens as _cleanup_consumed_tokens,
        cleanup_orphan_tokens as _cleanup_orphan_tokens,
        extract_command_context,
        _single_destructive_leg,
        _single_detectable_leg,
    )
    # Re-exports consumed by the test suite through this module's namespace
    # (module-level seam: tests import these via merge_guard_pre).
    # Read-floor danger predicates promoted to shared (GAP1/GAP5) — re-imported here.
    from shared.merge_guard_common import (  # noqa: F401  # re-export: test-suite seam
        _strip_non_executable_content,
        _has_pipe_to_shell,
        _has_process_substitution_to_shell,
        _has_eval_or_source,
        _var_is_expanded,
        _has_command_substitution,
        _has_eval_with_heredoc,
        TOKEN_TTL,
        # PR-number extraction relocated to shared (the SSOT extract_command_context
        # owns command-context derivation). _GH_PR_NUMBER_RE and _extract_pr_number
        # are re-exported here for the existing test imports.
        _GH_PR_NUMBER_RE,
        _extract_pr_number,
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

# Note: _GH_GLOBAL_FLAGS, _GH_FLAG_TOKENS, _GIT_GLOBAL_FLAGS, _GH_PREFIX,
# _GIT_PREFIX, _GH_API_PREFIX, _GH_PR_MERGE_RE, _GH_PR_CLOSE_RE are imported
# from shared.merge_guard_common above (#720 Bug B relocation).
# _MAX_GLOBAL_FLAG_TOKENS is also imported (bounds the shared-module flag walks:
# push read arms + mint push-to-main arm, #1001).

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
    # DANGEROUS_PATTERNS relocated to shared.merge_guard_common (GAP1); imported above.
    pass

    # _GH_PR_MERGE_RE, _GH_PR_CLOSE_RE, and _GH_PR_NUMBER_RE relocated to
    # shared.merge_guard_common (the operation-type classifier + PR-number
    # extraction that the shared extract_command_context owns). All are
    # imported back into this module at the top so direct callers here — and
    # the test imports — continue to resolve them.
except BaseException as _pattern_compile_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("pattern compilation", _pattern_compile_error)


# is_dangerous_command + is_compound_destructive_command + DANGEROUS_PATTERNS + the
# strip/_has_* closure + _COMPOUND_OPS_RE/_FD_REDIRECT_RE are now the SSOT in
# shared.merge_guard_common (GAP1/GAP5) and are imported at the top of this module so
# the read hook + mint hook call the SAME predicates. See that module.


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
        # Skip per-use slot markers (#720 Bug C). These are auxiliary files
        # (e.g., merge-authorized-N.use-1) created by _consume_token to
        # atomically claim a use slot; they are NOT tokens themselves and
        # have no JSON body to parse as one.
        # Substring-contains is correct by construction: the outer glob
        # `TOKEN_PREFIX*` constrains every basename to begin with
        # `merge-authorized-`, and tokens are written by `write_token` via
        # `tempfile.mkstemp` with controlled suffixes that never include
        # `.use-`. The only way `.use-` appears in a basename is via the
        # marker-file shape this check is designed to filter.
        if USE_MARKER_SUFFIX in os.path.basename(token_path):
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

            # Session scoping (SEC-S1 cycle-2 revised asymmetric predicate).
            token_session = token_data.get("session_id", "")
            if current_session:
                # SEC-S1 cycle-2: gate ONLY when current_session is populated.
                # When current_session=="" (no PACT context), preserve
                # graceful-degradation per architect §3 revised design.
                # Cycle-1 fail-OPEN-on-either-empty AND-short-circuit WAS
                # itself the attack surface — populated current_session +
                # empty token_session let attacker-written tokens through.
                # See test_no_session_id_accepts_any_token (in test_merge_guard.py)
                # for the preserved invariant; its SEC-S1 inversion counterpart
                # is the fix landing.
                if not token_session or current_session != token_session:
                    continue

            # Slot-claim filter (#720 Bug C). If max_uses > 1 and all use
            # slots are already claimed, the token is fully consumed even
            # if the terminal .consumed rename was lost to a transient FS
            # error. Treat as already-consumed (skip + race-recover the
            # rename). Legacy tokens missing max_uses are treated as N=1
            # and reach this point only when not yet consumed at all.
            token_max_uses = token_data.get("max_uses", 1)
            if isinstance(token_max_uses, int) and token_max_uses > 1:
                all_claimed = all(
                    os.path.exists(f"{token_path}{USE_MARKER_SUFFIX}{slot}")
                    for slot in range(1, token_max_uses + 1)
                )
                if all_claimed:
                    # Race-recover: attempt the missed terminal rename;
                    # ignore failure (the next scan will retry or the
                    # cleanup loop will eventually reap the markers).
                    try:
                        os.rename(token_path, token_path + ".consumed")
                    except OSError:
                        pass
                    continue

            # Valid token found
            valid_token = token_data
            valid_path = token_path

        except (json.JSONDecodeError, OSError, KeyError, AttributeError, TypeError):
            # Corrupted or malformed token — clean up
            _safe_remove(token_path)

    # Clean up stale consumed tokens while we're scanning
    _cleanup_consumed_tokens(token_dir)

    # Layer 3 (cross-cutting disk hygiene): reap unconsumed tokens older
    # than ORPHAN_TOKEN_MAX_AGE_SECONDS (12x TOKEN_TTL). Primary trigger
    # — runs on every dangerous-Bash precheck so orphans are bounded
    # within 12x TOKEN_TTL of the next destructive command. Fail-open by
    # construction; cleanup_orphan_tokens swallows all OSError paths.
    _cleanup_orphan_tokens(token_dir)

    return valid_token, valid_path


def _safe_remove(path: str):
    """Remove a file, ignoring errors if it doesn't exist."""
    try:
        os.unlink(path)
    except OSError:
        pass


def _consume_token(token_path: str, max_uses_default: int = 1) -> bool:
    """Consume one use of a token via per-use marker O_EXCL claim (#720 Bug C).

    Atomicity model:
    - Read the token JSON to determine max_uses. Tokens missing the field
      (written by pre-#720 versions) default to max_uses_default=1 —
      preserving prior single-use semantics for in-flight legacy tokens.
    - For each candidate slot in 1..max_uses, atomically attempt to claim
      `<token_path>.use-<slot>` via os.open(O_CREAT | O_EXCL). The FIRST
      successful claim is THIS invocation's use; the slot file's existence
      is the consume record.
    - If the claimed slot is the LAST slot (slot == max_uses), also rename
      the token to `.consumed` (terminal). Rename failure is non-fatal —
      `find_valid_token`'s slot-claim filter detects the all-claimed state
      on the next scan and race-recovers the rename.
    - If ALL slots are already claimed (FileExistsError on every slot),
      this invocation returns False — the token is fully consumed and the
      caller fails closed.

    Legacy compat path (max_uses == 1): skips slot markers entirely and
    invokes the original rename-to-.consumed flow, preserving exact prior
    semantics for tokens written by pre-#720 merge_guard_post.

    Concurrent-invocation safety: O_EXCL is POSIX-atomic, so two
    simultaneous invocations contending for the same slot resolve with
    exactly one winner; the loser falls through to the next slot.

    Args:
        token_path: Path to the valid (non-.consumed) token file.
        max_uses_default: Default max_uses when the token JSON lacks the
                          field (legacy compat). Callers from this module
                          rely on 1 to preserve prior single-use semantics
                          for older tokens.

    Returns:
        True if this invocation claimed a use slot, False otherwise.
    """
    max_uses = max_uses_default
    n_use_token = False  # Set True when we observe N>1 from JSON OR filesystem
    try:
        with open(token_path, "r") as f:
            token_data = json.load(f)
            field_value = token_data.get("max_uses", max_uses_default)
            if isinstance(field_value, int) and field_value > 0:
                max_uses = field_value
                if max_uses > 1:
                    n_use_token = True
    except (json.JSONDecodeError, OSError, KeyError, AttributeError, TypeError):
        # Read failure (corrupt JSON OR token already renamed to .consumed
        # after a prior slot-claim flow). If we can see any .use-N markers
        # on disk, this is an N-use token whose budget is being inspected
        # post-rename — fail closed rather than fall back to legacy
        # rename-idempotent semantics.
        if os.path.exists(f"{token_path}{USE_MARKER_SUFFIX}1"):
            return False
        max_uses = max_uses_default

    consumed_path = token_path + ".consumed"

    # Legacy single-use path (max_uses == 1, no slot markers visible):
    # identical to pre-#720 behavior — rename to .consumed, with the
    # FileNotFoundError ↔ .consumed-exists idempotency fallback preserved
    # verbatim.
    #
    # Idempotency model: under concurrent invocation, the first caller
    # wins the os.rename (atomic on POSIX); the second caller's rename
    # raises FileNotFoundError because the source no longer exists. The
    # fallback `os.open(consumed_path, O_RDONLY)` confirms the .consumed
    # file is on disk — meaning a prior invocation succeeded — and this
    # invocation returns True idempotently. A True return from EITHER
    # caller means "your operation is authorized"; legacy N=1 semantics
    # treat the first-success and the racing-recognition as equivalent.
    if max_uses <= 1 and not n_use_token:
        try:
            os.rename(token_path, consumed_path)
            return True
        except FileNotFoundError:
            try:
                fd = os.open(consumed_path, os.O_RDONLY)
                os.close(fd)
                return True
            except FileNotFoundError:
                return False
            except OSError:
                return False
        except OSError:
            return False

    # N-use path: claim slots via O_EXCL. The first slot we can claim is
    # this invocation's use; the last slot also triggers terminal rename.
    for slot in range(1, max_uses + 1):
        marker_path = f"{token_path}{USE_MARKER_SUFFIX}{slot}"
        try:
            fd = os.open(marker_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Slot already claimed by a prior or concurrent invocation —
            # try the next slot.
            continue
        except OSError:
            # Transient FS error claiming this slot — try the next slot
            # rather than fail closed, since another slot may still be
            # available.
            continue

        # Slot claimed. Write the audit body; on body-write failure, unlink
        # the marker so we don't leave an orphan slot blocking future uses
        # (mirrors merge_guard_post.write_token's body-write failure path).
        # The inner try/finally guarantees the raw fd is closed even if
        # os.fdopen itself raises before taking ownership (e.g., MemoryError
        # mid-construction). os.fdopen success transfers ownership of fd
        # to the file object, whose with-block close becomes the source of
        # truth; the outer os.close in the finally then raises EBADF, which
        # we suppress because that path indicates fdopen took ownership
        # already.
        fdopen_took_ownership = False
        try:
            try:
                file_obj = os.fdopen(fd, "w")
            except Exception:
                # fdopen failed before taking ownership — fd still ours.
                raise
            fdopen_took_ownership = True
            with file_obj as f:
                f.write(json.dumps({
                    "consumed_at": time.time(),
                    "slot": slot,
                }))
        except Exception:
            if not fdopen_took_ownership:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(marker_path)
            except OSError:
                pass
            continue

        # Audit emit — operator-visible stderr (mirrors existing [security]
        # and `Merge authorization token written` emits). Format is
        # invariant under MAX_USES changes.
        # Defense-in-depth: sanitize the basename's CR/LF characters before
        # interpolation to prevent log-line injection if a malicious local
        # actor planted a token file with a newline in its name. In practice
        # write_token uses tempfile.mkstemp with a controlled suffix so the
        # path can never contain CR/LF; this sanitization is belt-and-
        # suspenders for the audit-trail integrity story.
        safe_basename = (
            os.path.basename(token_path)
            .replace("\n", " ")
            .replace("\r", " ")
        )
        print(
            f"[security] merge-authorized token consumed "
            f"(slot {slot}/{max_uses}): {safe_basename}",
            file=sys.stderr,
        )

        # Terminal rename when the final slot is claimed. Rename failure is
        # non-fatal: find_valid_token will see all slots claimed on the
        # next scan and race-recover the rename then.
        if slot == max_uses:
            try:
                os.rename(token_path, consumed_path)
            except OSError:
                pass
        return True

    # Every slot was already claimed before we got there — token is fully
    # consumed. Caller fails closed.
    return False


# _GH_PR_VALUE_TAKING_FLAGS and _extract_pr_number relocated to
# shared.merge_guard_common (the shared extract_command_context owns PR-number
# extraction); _extract_pr_number is imported back at the top of this module.


def _both_present_equal(token_val: object, cmd_val: object) -> bool:
    """True iff BOTH values are present (not None) and string-equal.

    The positive-match primitive for the fail-closed read predicate: either
    side absent -> False, so an unextractable target REFUSES rather than falls
    through permissively. String comparison normalizes a numeric pr_number
    stored as int/str on either side.
    """
    if token_val is None or cmd_val is None:
        return False
    return str(token_val) == str(cmd_val)


def _token_matches_command(token: dict, command: str) -> bool:
    """Check that a token's context POSITIVELY matches the command being executed.

    Fail-closed (the #1031/#1032 mint-vs-read symmetry fix): the read side
    authorizes ONLY when the token and the command-derived context agree on
    BOTH the operation type AND that operation's target. Any axis that is
    unextractable, absent, or mismatched -> REFUSE. There is NO terminal allow:
    a malformed context, an untyped token, an unrecognized command shape, or a
    missing target all deny (the operator re-approves via AskUserQuestion). This
    closes the prior fall-through that let an untyped or target-less token
    authorize an unrelated destructive command (e.g. token{op=None} authorizing
    `git push --force origin main`).

    The command is classified via the shared `extract_command_context` SSOT, so
    the read side derives (op, target) the SAME way the mint side does — the two
    arms cannot drift.

    Args:
        token: Token data dict with optional context fields
        command: The bash command being authorized

    Returns:
        True ONLY when the operation type and the op's target both positively
        match; False otherwise.
    """
    context = token.get("context", {})
    if not isinstance(context, dict):
        return False  # F-READ-1: a non-dict context proves nothing — REFUSE

    token_op = context.get("operation_type")
    # Derive (op, target, bound_flags) from the SINGLE destructive leg, not the
    # whole command (#1069). When a faithful single destructive op carries a benign
    # neighbor leg (`gh pr close 1058 ; gh pr view 1058 --repo o/x`), the whole-
    # command context would (a) bind the neighbor's `--repo` into bound_flags and
    # over-block the faithful click, AND (b) let _extract_pr_number's first-match-
    # anywhere scan cross-contaminate the target (the latent under-block: a token
    # for `gh pr close N1` authorizing `gh pr close N1\ngh pr merge N2`). Isolating
    # the one is_dangerous leg closes BOTH. TWO-TIER fallback (#1083):
    # _single_destructive_leg returns None on not-exactly-one dangerous leg (0,
    # or — unreachably here, since is_compound REFUSES >=2 upstream — many);
    # tier 2 (_single_detectable_leg) then handles the EMERGENT-danger case —
    # whole-command danger from a cross-leg lookahead with NO individually-
    # dangerous leg (the close arms) — by binding from the unique detect-positive
    # leg, keeping the read bind symmetric with the mint's leg-bounded window.
    # Only when BOTH tiers abstain (0 or >=2 candidate legs — ambiguity) do we
    # fall back to the WHOLE command: the existing over-binding scan, the SAFE
    # over-block direction; ambiguity can only collapse WIDER, never narrower.
    # Op/target are still derived the SAME way the mint side does (mint
    # isolates per-region via locate_command_regions), so the two arms cannot drift.
    cmd = extract_command_context(
        _single_destructive_leg(command)
        or _single_detectable_leg(command)
        or command
    )
    cmd_op = cmd.get("operation_type")

    # (a) Operation-type axis — both present AND equal, else REFUSE. A token with
    # operation_type=None (or a command whose destructive shape is unrecognized)
    # can NEVER authorize: this denies the untyped-token fall-through (the A1/A2
    # hole) on the read axis rather than skipping the guard for it.
    if token_op is None or cmd_op is None or token_op != cmd_op:
        return False

    # (a2) NEVER-ESCALATE FLAG AXIS (#1042) — the executed command's binding-
    # relevant flags must EXACTLY equal the approved set. ANY difference (an added
    # privilege like --admin/-R, OR a dropped constraint) REFUSES. Both sides are
    # computed by the shared extract_command_context SSOT, so they cannot drift.
    # Checked AFTER op-type identity and BEFORE the per-op target returns so it
    # applies uniformly to all four op-classes. A pre-fix token without the key
    # defaults to the empty set, so any privileged execution mismatches -> REFUSE
    # (over-block-safe; tokens expire after TOKEN_TTL, so no backward-compat is needed).
    # bound_flags is an attribute checked here, never part of pair identity.
    if set(context.get("bound_flags", [])) != set(cmd.get("bound_flags", [])):
        return False

    # (b) Target axis, per op-class — require a POSITIVE, command-anchored target
    # match. Unextractable or mismatched target -> REFUSE (over-block is the safe
    # #1031 direction; the read side never under-blocks #1032).
    if token_op in ("merge", "close"):
        return _both_present_equal(context.get("pr_number"), cmd.get("pr_number"))
    if token_op == "branch-delete":
        return _both_present_equal(context.get("branch"), cmd.get("branch"))
    if token_op in ("force-push", "push-to-main", "remote-ref-delete"):
        # KD-6 (SECURITY-RATIFICATION-PENDING): the destination ref must match
        # explicitly — an op-type-only floor would let a 'force-push feature'
        # approval authorize 'force-push main'. An implicit/multi-ref/unparseable
        # ref is ABSENT in extract_command_context, so this REFUSES it. GAP3:
        # push-to-main is target-matched identically (its target is the main/master
        # ref), so a faithful plain-push token authorizes its own exec — while a
        # force-push token and a push-to-main token stay DISTINCT ops (op-type
        # identity is checked above), keeping the push→force collapse closed.
        # #1062a: remote-ref-delete ALSO binds on target_ref (the deleted ref); the
        # op-type identity checked above keeps a remote-ref-delete token from
        # cross-authorizing a force-push/push-to-main sharing the same target_ref
        # (the lead Q1 distinct-op-class guarantee).
        return _both_present_equal(context.get("target_ref"), cmd.get("target_ref"))
    if token_op == "remote-mass-delete":
        # #1062b: bind on the DISTINCT `mass_target` identity tuple (mass-flags +
        # remote + sorted refspecs). Distinct invocations → distinct tuples, so a
        # token minted for `git push --prune origin` (--prune@origin) does NOT
        # authorize `git push --mirror origin` (--mirror@origin) — the lesser→greater
        # / cross-form closure. An unextractable tuple is ABSENT → REFUSE.
        return _both_present_equal(context.get("mass_target"), cmd.get("mass_target"))
    if token_op == "branch-protection":
        # #1063: bind on the protected branch (PATH-resident, branches/<b>/protection).
        # op-type identity above keeps a branch-protection token from authorizing a
        # branch-delete of the same branch name (distinct op-classes). An unextractable
        # branch is ABSENT → REFUSE.
        return _both_present_equal(context.get("protected_branch"), cmd.get("protected_branch"))

    # Unknown op-class (a typed token whose op is not one of the handled classes)
    # — REFUSE. No terminal allow exists on the read path.
    return False


def check_merge_authorization(command: str, token_dir: Path | None = None) -> str | None:
    """Check if a dangerous command is authorized.

    Tokens are bounded-use, not single-use: one approval authorizes up to
    MAX_USES=2 identical-context attempts within TOKEN_TTL. A SUCCESSFUL
    operation immediately retires the token (renamed to .consumed) regardless
    of remaining uses (invariant I-2) — a success never leaves a reusable
    token — while a FAILED first attempt preserves the token so exactly one
    identical-context retry stays authorized within TTL (invariant I-4). The
    .use-N slot claims and the terminal .consumed rename are atomic on POSIX
    filesystems and idempotent — a concurrent invocation that already retired
    the token recognizes the .consumed file and allows the command. The token's
    context is validated against the command to ensure the approved operation
    matches what is being executed.

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
            # Token exists but its approved operation does not match this
            # command — don't consume it; block the mismatched command. Guide
            # the operator to re-approve with the literal command embedded so
            # the approved and executed commands are identical. NEVER suggest
            # running the bare command or simplifying the question (that would
            # teach guard evasion).
            return (
                "An authorization token exists but its approved operation does "
                "not match this command. The approved command and the executed "
                "command must be identical. Re-approve via AskUserQuestion with "
                "the literal command embedded in the selected option (e.g. "
                "`gh pr merge <N>`, the real PR number) so the guard binds the "
                "approval to this exact command."
            )

    # No token at all — require a fresh approval. Same guidance: embed the
    # literal command in the approval; never instruct running the bare command
    # or reducing the question. A channel/headless session has no
    # AskUserQuestion, so the operator approves the operation interactively
    # (a correct, expected over-block — documented here so it is not confusing).
    return (
        "Merge/close/force-push/branch-delete requires user approval. Re-approve "
        "via AskUserQuestion with the literal command embedded in the selected "
        "option (e.g. `gh pr merge <N>`, the real PR number) so the guard can "
        "bind the approval to this exact command. In a channel/headless session "
        "where AskUserQuestion is unavailable, approve the operation interactively."
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
