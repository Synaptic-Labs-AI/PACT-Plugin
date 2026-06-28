#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/merge_guard_post.py
Summary: PostToolUse hook matching AskUserQuestion + Bash — writes a short-lived
         authorization token on AskUserQuestion approval; retires the consuming
         token on successful `gh pr merge` Bash invocation.
Used by: hooks.json PostToolUse hook (matcher: AskUserQuestion|Bash)

This hook is part of the merge guard system. Two PostToolUse branches:

  AskUserQuestion branch (existing): when AskUserQuestion confirms a merge,
  close, force push, or branch deletion and the user answers affirmatively,
  a token file is written to ~/.claude/. The companion hook (merge_guard_pre.py)
  checks for this token before allowing dangerous commands.

  Bash branch (Layer 1 per #797, invariant I-2): on successful `gh pr merge`
  PostToolUse, the consuming token is atomically retired (.consumed) so it
  cannot be reused for a subsequent merge command. Observer-style by design
  per architect §13.4 — never blocks the tool call; retirement is observation,
  not permission decision.

Input: JSON from stdin with tool_name (AskUserQuestion or Bash), tool_input,
       and tool_response.
Output: None (side effect: writes or retires token file).
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_alert BEFORE wrapped imports) ─
import glob
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import NamedTuple, NoReturn


def _emit_load_failure_alert(stage: str, error: BaseException) -> NoReturn:
    """Stdlib-only fail-LOUD alert for module-load failure. PostToolUse cannot
    DENY (the tool already ran), so this is the nearest fail-closed equivalent.

    Channel semantics: on a non-zero exit the platform does NOT parse stdout
    JSON — for PostToolUse exit 2 the channel that reaches the model is
    STDERR. The complete advisory therefore lives on stderr; the stdout JSON
    below is forensic belt-and-braces only. Exit 2 (never 0) keeps the
    failure rc-visible — an additionalContext advisory + exit 0 would be
    rc-clean while bricked, the self-masking shape this gate family avoids.

    Both branches of this hook are skipped on load failure, hence the two
    consequences named in the message: the Bash branch cannot RETIRE a
    consumed merge token, and the AskUserQuestion branch cannot WRITE new
    authorization tokens.
    """
    message = (
        f"PACT merge_guard_post {stage} failure — merge-token write/retirement "
        f"SKIPPED this turn; a consumed token may remain live, and subsequent "
        f"merge approvals will also fail to record — expect merge_guard_pre "
        f"denials until fixed. {type(error).__name__}: {error}. Check hook "
        "installation and shared module availability."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        }
    }))
    print(
        f"Hook load error (merge_guard_post / {stage}): {message}",
        file=sys.stderr,
    )
    sys.exit(2)


# ─── fail-loud wrapper on cross-package imports ─────────────────────────────
try:
    import shared.pact_context as pact_context
    from shared.pact_context import get_session_id

    # Shared constants and cleanup — single source of truth for both hooks
    sys.path.insert(0, str(Path(__file__).parent))
    from shared.error_output import hook_error_json

    from shared.merge_guard_common import (
        TOKEN_TTL,
        TOKEN_DIR,
        TOKEN_PREFIX,
        MAX_USES,
        USE_MARKER_SUFFIX,
        LAYER1_SUCCESS_STDOUT_PATTERNS,
        cleanup_consumed_tokens as _cleanup_consumed_tokens,
        cleanup_unused_tokens as _cleanup_unused_tokens,
        detect_command_operation_type,
        extract_command_context,
        locate_command_region,
        locate_command_regions,
    )
    from shared.tool_response import extract_tool_response
except BaseException as _module_load_error:  # noqa: BLE001 — fail-loud catch-all
    _emit_load_failure_alert("module imports", _module_load_error)


# Command-region finding is owned by the shared SSOT (locate_command_regions /
# locate_command_region in merge_guard_common): both arms locate + classify the
# SAME command string, so the mint and read sides cannot drift. The former
# post-local _QUOTED_COMMAND_RE / _classify_from_quoted_command are subsumed.

# When the hook allows a command (exits 0), output this JSON so the Claude Code
# UI suppresses the hook display instead of showing "hook error (No output)".
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Patterns that indicate an affirmative FREE-TEXT answer. Used ONLY by the
# free-text arm (an AskUserQuestion with no options); OPTION-mode approval is an
# exact label match against a non-decline option, never a word allowlist — so a
# descriptive selected label like "Merge now" is honored (the old allowlist
# wrongly rejected it). The decline/defer veto runs first and takes precedence.
AFFIRMATIVE_PATTERNS = re.compile(
    r"^(y|yes|yeah|yep|sure|ok|okay|confirm|approved?|go\s*ahead|do\s*it|proceed)\b",
    re.IGNORECASE,
)


def is_merge_question(question: str) -> bool:
    """Command-driven COARSE HINT (KD-9): True iff the text embeds a recognized
    destructive command (gh/git merge / close / force-push / branch-delete) that
    the shared classifier identifies via locate_command_region.

    This is NOT a keyword matcher and NOT the security gate — terse prose with no
    embedded command no longer false-fires, and an over-fire is harmless (no
    located command → no mint). The boundary is the option-command + decline veto
    + read-side fail-closed predicate, not question prose.

    Args:
        question: The question text from AskUserQuestion

    Returns:
        True if the text contains a recognized destructive command region.
    """
    return locate_command_region(question) is not None


def is_affirmative(answer: str) -> bool:
    """Check if the user's answer is affirmative.

    Args:
        answer: The user's response text

    Returns:
        True if the answer indicates approval
    """
    return bool(AFFIRMATIVE_PATTERNS.search(answer.strip()))


def _selected_option_text(options: object, answer: object) -> str | None:
    """Return "<label> <description>" of the option whose label EXACTLY matches
    the answer (option mode), or None when there are no options, no exact-label
    match, or the inputs are malformed. Labels are unique within a question (AUQ
    contract); the byte-equal match is the D3 source-guarantee (no fuzzy match)."""
    if not isinstance(options, list) or not options:
        return None
    if not isinstance(answer, str):
        return None
    for opt in options:
        if not isinstance(opt, dict):
            continue
        label = opt.get("label")
        if not isinstance(label, str) or label != answer:
            continue
        description = opt.get("description")
        description = description if isinstance(description, str) else ""
        return (label + " " + description).strip()
    return None


def _target_value(cmd_ctx: dict) -> str | None:
    """The op-class target value (pr_number / branch / target_ref) from an
    extracted command context, or None. A located region is a COMPLETE
    (op, target) pair only when this is non-None — an op without a target
    contributes NO pair to the multiplicity gate."""
    return (
        cmd_ctx.get("pr_number")
        or cmd_ctx.get("branch")
        or cmd_ctx.get("target_ref")
    )


def _collect_pairs(texts: list) -> dict:
    """Map every COMPLETE (op_type, target) pair found across `texts` to the
    command region that produced it (locate_command_regions + extract_command_context
    + _target_value). A region with an op but no target contributes no pair."""
    pairs: dict = {}
    for text in texts:
        for region in locate_command_regions(text):
            cmd_ctx = extract_command_context(region)
            op_type = cmd_ctx.get("operation_type")
            target = _target_value(cmd_ctx)
            if op_type is not None and target is not None:
                pairs.setdefault((op_type, target), region)
    return pairs


class MintResult(NamedTuple):
    """Outcome of `_mint_context_from_bundle` (B2 / #1052) — exactly one field is
    non-None. `context` is the dict to mint when the bundle authorizes exactly ONE
    destructive command; `refusal_reason` is a `_REFUSAL_DIAGNOSTICS` key naming
    WHY a command-bearing approval did not mint. The refusal_reason is consumed
    ONLY by the observer-style no-mint advisory in `main` — it NEVER feeds the
    authorization path (no token derives from it)."""

    context: dict | None
    refusal_reason: str | None


# B2 (#1052): self-teaching diagnostic per refusal reason code, surfaced ONLY by
# the observer-style no-mint advisory in `main` (gated to command-bearing
# bundles). These NAME the failing gate; they never authorize.
_REFUSAL_DIAGNOSTICS = {
    "no_options": "the approval carried no clickable options (a free-text answer never mints)",
    "label_mismatch": (
        "the answer did not exactly match an option label — if the command was "
        "in the option LABEL, move it into the option DESCRIPTION (a long "
        "command-as-label can fail to round-trip)"
    ),
    "no_command": "no recognized merge/close/force-push/branch-delete command was found in the selected option",
    "multiple_commands": "more than one distinct (operation, target) was present — the approval was ambiguous",
    "option_not_anchored": "the command appeared only in the question text, not in the selected option",
}


# #1059 (mint-side): the #1042 privileged-flag scan runs over the WIDER selected-
# option text (markdown-wrapped), so a command-WRAPPING backtick/curly-quote can be
# captured into a flag VALUE (e.g. `--repo owner/x` → owner/x with a trailing
# backtick), desyncing from the read side which scans the bare executed command →
# set-equality REFUSES a faithful approval. Space-REPLACE (never delete — preserves
# token boundaries: "owner/x`--admin" → "owner/x --admin", revealing --admin rather
# than gluing it) the wrapper delimiters so the mint scans the same surface the read
# side does → mint bound_flags == read bound_flags for a faithful command. Backtick
# is the canonical wrapper (peer-review.md); curly quotes are prose-only. Straight
# quotes are LEFT to shared `_strip_surrounding_quotes` (symmetric both sides; moot —
# a --repo value is never quoted). This NORMALIZES the scan surface ONLY; it can only
# remove a wrapper char from the mint surface (never add a flag, never touch the read
# side) → an under-block is structurally impossible, and the #1042 comparator stays
# EXACT set-equality (an exec-time EXTRA privileged flag still REFUSES).
_FLAG_SCAN_WRAPPER_TABLE = {ord(c): " " for c in "`‘’“”"}


def _strip_command_wrapper(flag_scan_text: str) -> str:
    """#1059: space-replace markdown command-wrapper delimiters (backtick + curly
    quotes) on the mint's flag-scan surface so its #1042 privileged-flag set matches
    the read side's on the bare command. See `_FLAG_SCAN_WRAPPER_TABLE` rationale."""
    return flag_scan_text.translate(_FLAG_SCAN_WRAPPER_TABLE)


def _mint_context_from_bundle(questions: list, answers: dict) -> MintResult:
    """Decide whether an AskUserQuestion bundle authorizes exactly ONE destructive
    command; return a `MintResult(context, refusal_reason)` — `context` is the dict
    to mint, or None (with a `refusal_reason` code) to refuse.

    PURE-FLOOR MINIMAL gate — the operator's CONSENT is the CLICK on a command-
    bearing option; the surrounding prose (the option's label/description, the
    question text) is NEVER read to second-guess that click. Two check classes
    differ: (1) intent-GUESSING (does prose look like a decline/affirmation?) can
    false-block a genuine approval → REMOVED entirely; (2) the binding floor (does a
    clicked option carry exactly one command, and does the token bind to THAT
    command?) is FP-free → it stays. The flow:

      0. NO-OPTIONS GUARD — a bundle with no options has no clicked option to
         anchor on → refuse (free-text / zero-options never mints).
      2. CLICKED OPTION (option mode, fail-closed) — the mint source is the
         SELECTED option (exact label match; no match for a populated answer
         REFUSES, never falls back to free-text). multiSelect never mints.
      3. MULTIPLICITY — count DISTINCT (op_type, target) pairs across the CLICKED
         options only: ==1 mints, >1 refuses (a single click carrying 2 commands),
         ==0 yields no token. The question prose is NOT scanned.
      3b. OPTION ANCHORING — the minted (op,target) MUST be carried by a CLICKED
         option (true by construction, since Step 3 scans only the clicked options).
      5. extract_command_context(the single distinct command) → the mint context,
         binding op/target (#1031/#1032) + privileged flags (#1042 set-equality).

    ACCEPTED RESIDUALS (the user explicitly chose the most-minimal gate, eyes-open
    on these — they are a ratified S5 scope decision, NOT under-block bugs): the
    gate trusts the CLICKED option's command and does not cross-check the
    surrounding prose, so a malformed-authoring option mints —
      • #21 decline-LABELED option that nonetheless CARRIES the command (label
        "Don't merge"/"Cancel", description carries `gh pr merge 5`) → MINTS;
      • wrong-OP (label "Yes, close" on a merge command) → MINTS the merge;
      • wrong-TARGET (question shows "merge 5", clicked option carries "merge 7")
        → MINTS 7 (the clicked command);
      • sarcasm ("Yes, merge" meant ironically) → MINTS.
    The no-operator-decline-under-block property rests on a USER-ACCEPTED THREAT-
    MODEL ASSUMPTION (not a proof): the agent places the destructive command ONLY
    in affirmative options, never a decline-labeled one — so a genuine decline
    option carries no command → fails anchoring → no mint. NOT a residual:
    post-click escalation/substitution — the #1042 set-equality bind still REFUSES
    an execution-time EXTRA privileged flag or a changed (op,target).
    """
    # The operator's ACTION SURFACE = the CLICKED option(s); free-text never mints.
    selected_option_texts: list[str] = []

    # EXPLICIT (shape b): a bundle with NO options anywhere has no clicked option
    # to anchor on → it can NEVER mint. Minting from question prose or a typed
    # free-text answer would make auth depend on un-clicked text — a forbidden
    # under-block class (security B-NEW-4 / KD-11(6)). AUQ structurally carries
    # 2-4 options per question (peer-review.md), so this is fail-closed defense
    # for a theoretical/replayed no-options payload. The per-question decline/
    # defer veto still runs below for mixed bundles.
    #
    # BACKSTOP — DO NOT remove as "dead code". The step-3b option-anchoring below
    # already refuses a no-options bundle (an empty option surface yields no pair,
    # so the minted (op,target) is never ∈ it → None), making this early return
    # functionally redundant TODAY. It is kept DELIBERATELY so the "free-text /
    # no-options never mints" rule is STATED here at the top, not left emergent
    # from a downstream gate that a future edit could weaken without noticing.
    if not any(
        isinstance(q, dict) and isinstance(q.get("options"), list) and q.get("options")
        for q in questions
    ):
        return MintResult(None, "no_options")

    for q in questions:
        if not isinstance(q, dict):
            continue
        qtext = q.get("question", "")
        qtext = qtext if isinstance(qtext, str) else ""
        options = q.get("options", [])
        options = options if isinstance(options, list) else []
        multi = bool(q.get("multiSelect", False))
        # KD-12: key the answer to its SPECIFIC question (no iter-fallback).
        answer = answers.get(qtext)

        # ── multiSelect never mints — refused as a mint SOURCE (over-block-safe
        # structural guard). PURE-FLOOR MINIMAL: NO decline-intent / label-prose
        # parsing of any kind. The operator's CONSENT is the CLICK on a command-
        # bearing option, validated ONLY by anchoring + multiplicity + the
        # #1042/#1031/#1032 binding below — never by reading the option's or the
        # question's prose for decline/affirmation intent. ──
        if multi:
            continue

        # ── Step 2: resolve the operator's CLICKED option (option mode only). ──
        if options:
            # The mint source is the SELECTED option (exact label match). No exact
            # match for a populated answer → REFUSE; NEVER fall back to free-text
            # (B-NEW-2). An "Other" freeform answer to an options question matches
            # no label → also refused here.
            selected_text = _selected_option_text(options, answer)
            if selected_text is None:
                if isinstance(answer, str) and answer:
                    return MintResult(None, "label_mismatch")
                continue  # empty/missing answer → no clicked option for this q
            selected_option_texts.append(selected_text)
        # else: a no-options (free-text) question contributes NO mint source; its
        # answer was already decline/defer veto-scanned in Step 1 (and a pure
        # free-text bundle already refused at the no-options guard above).

    # ── Step 3: distinct-(op,target) multiplicity over the CLICKED options ONLY
    # (pure-floor scope — the question prose is NOT cross-checked): ==1 mints, >1
    # refuses (a single click carrying 2 commands), ==0 yields no token. ──
    bundle_pairs = _collect_pairs(selected_option_texts)
    if len(bundle_pairs) != 1:
        return MintResult(
            None, "no_command" if len(bundle_pairs) == 0 else "multiple_commands"
        )
    (the_op, the_target), the_command = next(iter(bundle_pairs.items()))

    # ── Step 3b: OPTION ANCHORING (#1032) — the minted (op,target) MUST be carried
    # by a CLICKED option. Under the pure-floor scope (Step 3 scans ONLY the clicked
    # options) this holds BY CONSTRUCTION; kept as an explicit invariant so a future
    # edit that re-widens the multiplicity scan can never authorize a command carried
    # by question prose alone. ──
    if (the_op, the_target) not in _collect_pairs(selected_option_texts):
        return MintResult(None, "option_not_anchored")

    # ── Step 5: extract the single distinct command's context to mint. Op/target
    # are derived from `the_command` (region-anchored). The privileged-flag scan
    # (#1042) is widened to the FULL selected-option text so a flag after a quoted
    # argument is not lost to bare-command truncation; #1059: the wrapping backtick/
    # curly delimiters are space-stripped first (_strip_command_wrapper) so the
    # mint's flag set matches the read side's on the bare command — the #1042
    # set-equality stays EXACT, so an exec-time EXTRA flag still REFUSES. ──
    return MintResult(
        extract_command_context(
            the_command,
            flag_scan_text=_strip_command_wrapper(" ".join(selected_option_texts)),
        ),
        None,
    )


def _bundle_has_command(questions: list) -> bool:
    """True if a recognized destructive command appears ANYWHERE in the bundle —
    question prose OR any option's label/description. Gates the B2 (#1052) no-mint
    advisory so it fires ONLY on a plausible merge-approval attempt and stays
    SILENT on benign, non-destructive AskUserQuestions (this hook matches EVERY
    AskUserQuestion, so an ungated advisory would spam ordinary questions). Uses
    the same shared `locate_command_region` classifier as the mint/read sides, so
    the gate cannot drift from the mint's own command detection."""
    for q in questions:
        if not isinstance(q, dict):
            continue
        qtext = q.get("question", "")
        if isinstance(qtext, str) and locate_command_region(qtext) is not None:
            return True
        options = q.get("options", [])
        if not isinstance(options, list):
            continue
        for opt in options:
            if not isinstance(opt, dict):
                continue
            blob = str(opt.get("label", "")) + " " + str(opt.get("description", ""))
            if locate_command_region(blob) is not None:
                return True
    return False


def _no_mint_advisory(refusal_reason: str) -> str:
    """Build the observer-style self-teaching no-mint advisory text (B2 / #1052):
    NAME the specific failing gate, then point at the canonical peer-review.md
    approval template. Pure string builder — emitted as `additionalContext` with
    exit 0; it NEVER writes a token or authorizes anything."""
    why = _REFUSAL_DIAGNOSTICS.get(
        refusal_reason, "the approval did not match the canonical form"
    )
    return (
        "PACT merge-guard: an AskUserQuestion approval was issued but NO "
        "authorization token was minted — " + why + ". The merge is HELD until a "
        "matching approval is given. Re-approve via the canonical template in "
        "pact-plugin/commands/peer-review.md: a SINGLE-select question, a short "
        '"Yes, merge" affirmative label, and the command in THAT option\'s '
        "DESCRIPTION in backticks (e.g. Run `gh pr merge <N>` to merge this PR), "
        "with <N> the only number anywhere in the prompt."
    )


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

    # Never mint a token without an operation_type. The read side
    # (merge_guard_pre._token_matches_command) is fail-closed on op_type and
    # denies any token whose op_type is absent — so an untyped token could never
    # authorize a command anyway. Refusing to write it keeps the on-disk token
    # set free of un-authorizable wildcards (defense-in-depth with the read-side
    # floor; placed AFTER the #700 sparse-context guard).
    if not has_op:
        print(
            "[security] refusing token write: no operation_type — an untyped "
            "token cannot positively match any command on the fail-closed read "
            "side.",
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
        # N-use budget (#720 Bug C). Reader (merge_guard_pre._consume_token)
        # claims one use-slot per invocation via O_EXCL on a per-use marker
        # file; the final slot also triggers terminal rename to .consumed.
        # Tokens written by pre-#720 versions lack these fields and are
        # treated by the reader as max_uses=1 (single-use, legacy semantics).
        "max_uses": MAX_USES,
        "uses_remaining": MAX_USES,
    }
    if session_id:
        token_data["session_id"] = session_id

    token_path = token_dir / f"merge-authorized-{timestamp}"

    # Layer 5 (invariant I-1): atomically retire any prior unused token
    # immediately before the new one is created. Placement BEFORE the
    # O_EXCL create is invariant-critical — placing it AFTER would leave
    # a window where two unused tokens coexist on disk. POSIX rename
    # atomicity provides race-safety against concurrent writers and
    # against the read-side _consume_token retirement path.
    _cleanup_unused_tokens(token_dir)

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


def _retire_token_for_command(
    command: str,
    op_type: str,
    token_dir: Path | None = None,
) -> bool:
    """Atomically retire (rename to .consumed) the consuming token for a
    successful destructive command. Observer-style per architect §13.4 —
    never raises; never blocks the caller; degrades to no-op on any
    failure (the TTL/MAX_USES safety net catches the token through the
    existing expiry path).

    Supports invariant I-2: successful operation immediately retires the
    token regardless of MAX_USES counter.

    SEC-S2 cycle-2: extended from merge-only to op_type-symmetric. Caller
    supplies the validated op_type (matching one of the keys in
    LAYER1_SUCCESS_STDOUT_PATTERNS). The op_type is filtered against the
    token's stored `operation_type` for symmetric retirement across
    merge/close/branch-delete/force-push.

    Emits a path-annotated stderr forensic log when retirement is
    observed (BC-NIT addressed: log line distinguishes "direct"
    rename-by-this-session from "race-recover" observed-by-this-session
    where another path — Layer 5 cleanup, _consume_token terminal
    rename, or cleanup_orphan_tokens unlink — won the race). SEC-S2
    extends the annotation with op_type so forensic operators can
    distinguish merge vs close vs branch-delete vs force-push retirement.

    Args:
        command: The Bash command string. Filtered by caller via Block 1.
        op_type: The validated op_type (caller has confirmed it is in
            LAYER1_SUCCESS_STDOUT_PATTERNS). Required positional — caller
            always knows op_type by the time it calls this helper; an
            optional default could mask a missing-op_type bug.
        token_dir: Override token directory (defaults to TOKEN_DIR).

    Returns:
        True if a token was retired (or concurrently retired by another
        path — race-recover treats both as success). False if no matching
        token was found.
    """
    if token_dir is None:
        token_dir = TOKEN_DIR
    pattern = str(token_dir / f"{TOKEN_PREFIX}*")
    current_session = get_session_id()
    for path in glob.glob(pattern):
        basename = os.path.basename(path)
        # Skip terminal-rename siblings and per-use markers (mirrors the
        # scan-pattern in merge_guard_pre.find_valid_token).
        if path.endswith(".consumed"):
            continue
        if USE_MARKER_SUFFIX in basename:
            continue
        try:
            with open(path, "r") as f:
                token_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        ctx = token_data.get("context", {})
        if not isinstance(ctx, dict):
            continue
        # SEC-S2: match by op_type (parameterized) rather than hard-coded
        # "merge". Context-specific PR-number matching belongs to the
        # PRE side's _token_matches_command; for Layer 1 retirement,
        # op_type match + session-scope is the minimum to retire (a
        # subsequent op of the same type would need a fresh token anyway).
        if ctx.get("operation_type") != op_type:
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
            # for the preserved invariant; its SEC-S1 inversion counterpart is
            # the fix landing.
            if not token_session or current_session != token_session:
                continue
        try:
            os.rename(path, path + ".consumed")
            print(
                f"[security] merge-authorization token retired "
                f"(via direct, op_type={op_type}) on successful "
                f"{op_type} command",
                file=sys.stderr,
            )
            return True
        except (FileNotFoundError, OSError):
            # Concurrent retire (Layer 5 cleanup, _consume_token terminal
            # rename, or another PostToolUse fire) won the race, OR
            # cleanup_orphan_tokens unlinked the token entirely. Either way
            # the token is no longer authorizable. We did NOT perform the
            # rename ourselves; the path-annotated log line distinguishes
            # this from "direct" for forensic precision.
            print(
                f"[security] merge-authorization token retired "
                f"(via race-recover, op_type={op_type}) on successful "
                f"{op_type} command",
                file=sys.stderr,
            )
            return True
    return False


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

        # Layer 1 Bash branch (invariant I-2 per #797): on successful
        # destructive Bash PostToolUse, retire the consuming token so it
        # cannot be reused for a subsequent same-op-type command.
        #
        # SEC-S2 cycle-2: extended from merge-only to op_type-symmetric.
        # The lookup table LAYER1_SUCCESS_STDOUT_PATTERNS (in
        # shared/merge_guard_common.py) drives both Block 1 op_type
        # acceptance and Block 3 stdout-pattern matching per op_type.
        # force-push uses None in the table — Block 3 is skipped for
        # that op_type, the 3-block predicate degrades to 2 blocks
        # (fail-closed-on-no-signal per architect §5 rationale).
        #
        # 3-block named-with-early-return REJECT pattern (architect §3.2).
        # NOT a first-match-wins dispatch precedence — each block
        # independently short-circuits to no-op on its own reject
        # condition. Structurally equivalent to a flat AND chain but
        # inspectable per-block.
        #
        # Observer-style per architect §13.4: token retirement is
        # observation, NOT a permission decision. The Bash branch always
        # exits 0 / suppressOutput regardless of retirement outcome.
        #
        # Hook stdin envelope (§13.6 deferred verification): the canonical
        # platform-shape per Claude Code Agent SDK docs is
        # `tool_response: {stdout: str, stderr: str, interrupted: bool}`
        # for successful Bash. In-session smoke-test instrumentation is
        # structurally not viable (CLAUDE.md "Hooks cannot be smoke-tested
        # against the running plugin in-session" pin). Field-name
        # mismatch degrades to no-retirement (fail-closed-on-uncertainty);
        # the existing TOKEN_TTL/MAX_USES safety net still bounds the
        # token.
        tool_name = input_data.get("tool_name", "")
        if tool_name == "Bash":
            command = (
                tool_input.get("command", "") if isinstance(tool_input, dict) else ""
            )

            # Block 1 — Command-shape filter (SEC-S2: extends from merge-
            # only to all op_types in LAYER1_SUCCESS_STDOUT_PATTERNS).
            # An op_type of None (classifier didn't recognize the command)
            # OR an op_type not in the table is rejected. The `in` check
            # against the dict keys is the SSOT for which op_types Layer 1
            # observes.
            op_type = detect_command_operation_type(command)
            if op_type not in LAYER1_SUCCESS_STDOUT_PATTERNS:
                print(_SUPPRESS_OUTPUT)
                sys.exit(0)

            # Block 2 — Platform-level success signal (dict-shape +
            # non-interrupted). PostToolUse only fires on tool-call
            # success at the platform layer (failed Bash routes to
            # PostToolUseFailure per Agent SDK); the dict-shape vs
            # non-dict asymmetry is a structural success boundary.
            if not isinstance(tool_response, dict):
                print(_SUPPRESS_OUTPUT)
                sys.exit(0)
            if tool_response.get("interrupted") is True:
                print(_SUPPRESS_OUTPUT)
                sys.exit(0)

            # Block 3 — gh CLI / git semantic signal via lookup table
            # (SEC-S2). Each op_type maps to its canonical success
            # substring. A None value means "skip Block 3 for this
            # op_type" — the predicate degrades to 2 blocks. force-push
            # uses None because git push --force emits primarily to
            # STDERR not STDOUT; substring-matching STDOUT for force-push
            # is structurally fragile. Block 2's platform-success
            # implication is the load-bearing check for force-push
            # (fail-closed-on-no-signal — no retirement degrades to
            # TTL/MAX_USES safety net, NOT bypass).
            expected_substring = LAYER1_SUCCESS_STDOUT_PATTERNS[op_type]
            if expected_substring is not None:
                stdout_text = tool_response.get("stdout", "")
                if not isinstance(stdout_text, str):
                    print(_SUPPRESS_OUTPUT)
                    sys.exit(0)
                if expected_substring not in stdout_text:
                    print(_SUPPRESS_OUTPUT)
                    sys.exit(0)

            # All applicable blocks passed → retire the consuming token.
            # Observer-style call: stderr forensic log is emitted INSIDE
            # _retire_token_for_command with path-annotation
            # ("(via direct, op_type=X)" or "(via race-recover, op_type=X)")
            # for forensic precision per BC-NIT + SEC-S2 OQ-BC-3.
            # Return value is intentionally NOT used to gate exit.
            _retire_token_for_command(command, op_type)
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        # Fall through to the AskUserQuestion mint path.

        # tool_input: {"questions": [{"question": "...", "options": [...], ...}]}
        if not isinstance(tool_input, dict):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        questions = tool_input.get("questions", [])
        if not isinstance(questions, list) or not questions:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # tool_response: {"answers": {"<question text>": "<selected label>"}, ...}
        answers = tool_response.get("answers", {}) if isinstance(tool_response, dict) else {}
        if not isinstance(answers, dict):
            answers = {}

        # Mint ONLY when the bundle authorizes exactly one destructive command:
        # decline/defer veto-first (precedence over command presence), bimodal
        # fail-closed selection, the SACROSANCT distinct-(op,target)==1
        # multiplicity gate, and label<->description op-consistency.
        # _mint_context_from_bundle returns MintResult(context, refusal_reason):
        # context is None to refuse (the over-block-safe #1031 direction) with a
        # refusal_reason code; a non-None context is op-typed + target-anchored, so
        # write_token's fail-closed guards always pass for a real approval.
        context, refusal_reason = _mint_context_from_bundle(questions, answers)
        if context is not None:
            token_path = write_token(context)
            if token_path:
                # Defense-in-depth parity with merge_guard_pre.py M-sec-2:
                # sanitize newline and carriage-return characters from the
                # path before interpolation to prevent log-line injection.
                # In practice `write_token` builds the path from `TOKEN_DIR`
                # (hardcoded `~/.claude/`) and a timestamp-derived filename
                # via `tempfile.mkstemp`, so neither segment can contain
                # CR/LF — this sanitization is belt-and-suspenders for the
                # audit-trail integrity story. Full path retained (not just
                # basename) so operators can locate the token file directly
                # from the log line during triage.
                safe_token_path = token_path.replace("\n", " ").replace("\r", " ")
                print(
                    f"Merge authorization token written: {safe_token_path}",
                    file=sys.stderr,
                )
        elif refusal_reason is not None and _bundle_has_command(questions):
            # B2 (#1052): an approval was issued for a command-bearing bundle but
            # minted NO token. Emit a self-teaching, OBSERVER-STYLE advisory that
            # NAMES the failing gate + points to the canonical peer-review.md
            # template. additionalContext + exit 0 — it NEVER writes a token or
            # authorizes; the pre-hook still denies, so a genuine merge stays HELD
            # (authorization isolation). Gated by _bundle_has_command so a benign,
            # non-destructive AskUserQuestion stays silent (suppressOutput below).
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": _no_mint_advisory(refusal_reason),
                }
            }))
            sys.exit(0)

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        # Never block on errors — this is an observer hook
        print(f"Hook warning (merge_guard_post): {e}", file=sys.stderr)
        print(hook_error_json("merge_guard_post", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
