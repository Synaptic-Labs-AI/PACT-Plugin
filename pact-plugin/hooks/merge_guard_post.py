#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/merge_guard_post.py
Summary: PostToolUse hook matching AskUserQuestion + Bash — writes a short-lived
         authorization token on AskUserQuestion approval; retires the consuming
         token on successful `gh pr merge` Bash invocation. On PostToolUseFailure
         (Bash) it retires the token once the guarded op has run, unless the
         call's output shows that op's own failure.
Used by: hooks.json PostToolUse hook (matcher: AskUserQuestion|Bash) and
         PostToolUseFailure hook (matcher: Bash)

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
import secrets
import shlex
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
        # GAP1/GAP5 read-floor predicates (promoted to shared): the mint gates its
        # token-write on is_dangerous_command (mint⊆read by construction) and refuses
        # any compound via is_compound_destructive_command — the SAME predicates the
        # read hook uses, so mint and read can never disagree on danger/compound.
        is_dangerous_command,
        is_compound_destructive_command,
        set_command_directory,
        # An interpreter program's guarded command has no contiguous `gh pr merge`
        # for the region finder; the pre-hook's own interpreter reading supplies its
        # (op, target, flags), and the token binds the command's normalized text.
        interpreter_command_context,
        normalize_command_text,
        exact_text_matches,
        # The command the classifier judges after rewriting this one (an eval of
        # readable text, a file written then run, a literal variable, a script
        # given a whole command); the mint reads that command, binding its text.
        rewritten_executed_command,
        unreadable_evald_heredoc,
        shell_c_string,
        # A git alias's run retires as the command its value names.
        _rewrite_git_aliases,
        # An eval of an assigned variable retires as the eval of its value.
        _rewrite_variable_evals,
        # An if/elif/else or case whose branches run different guarded commands
        # binds its text: no one branch's (op, target) stands for the command.
        alternative_destructive_branch_count,
        _has_command_substitution,
        # #1083: the mint flag-scan window runs the SAME two-tier leg selection
        # (unique dangerous leg -> unique detectable leg) the read bind seam runs,
        # so mint and read bind over identical leg semantics BY CONSTRUCTION —
        # not via two parallel reimplementations that can rot apart.
        _single_destructive_leg,
        _single_detectable_leg,
        _split_into_legs,
        # The mint's reading of a printed or eval'd string, for the dry-run check.
        _rewrite_readable_evals,
        # An op on the current branch run after a checkout or cd binds its text.
        target_changes_before_guarded_leg,
        REPO_RELATIVE_OPS,
        binds_a_masked_value,
        # A merge or close after an exported GH_REPO binds its text.
        exported_gh_target_before_guarded_leg,
        # The pre-hook's token match: a click mints a token that match accepts.
        _token_matches_command,
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
    """The op-class target value (pr_number / branch / branch_set / target_ref /
    push_set / mass_target / protected_branch) from an extracted command context, or None.
    A located region is a COMPLETE (op, target) pair only when this is non-None — an op
    without a target contributes NO pair to the multiplicity gate.

    MINT-SIDE FOUR-SITE ELEMENT (#1064): every op-class that uses a NEW context key MUST
    be enumerated here, or the mint cannot extract its target → the op gates on the read
    side but is gated-but-unmintable. remote-ref-delete reuses `target_ref` (already
    listed); remote-mass-delete (#1062b) uses the distinct `mass_target`; branch-protection
    (#1063) uses `protected_branch`; multi-branch force-delete (#1129) uses the distinct
    `branch_set` (its scalar single-branch sibling `branch` is already listed);
    multi-ref push-to-main (#1195 OBS-G) uses the distinct `push_set` and multi-ref
    force-push (#1195 OBS-I) the distinct `force_push_set` (their scalar single-ref
    sibling `target_ref` is already listed); the IMPLICIT current-branch force-push
    (#1203) uses the distinct target-blind `force_push_implicit` sentinel and the bare
    `gh pr merge` (#1203) the distinct target-blind `merge_implicit` sentinel (its
    scalar sibling `pr_number` is already listed) — each added here so that op MINTS,
    not just gates."""
    return (
        cmd_ctx.get("pr_number")
        or cmd_ctx.get("merge_implicit")
        or cmd_ctx.get("branch")
        or cmd_ctx.get("branch_set")
        or cmd_ctx.get("target_ref")
        or cmd_ctx.get("push_set")
        or cmd_ctx.get("force_push_set")
        or cmd_ctx.get("force_push_implicit")
        or cmd_ctx.get("mass_target")
        or cmd_ctx.get("protected_branch")
    )


def _extraction_surface(region: str) -> str:
    """The surface extract_command_context consumes for a region (#1195 OBS-H) — the
    IDENTICAL three-tier expression the read side's _token_matches_command uses at
    execution time (tier-1 unique dangerous leg; tier-2 unique detectable leg, the
    emergent-danger case; else the whole region = the pre-OBS-H behavior, the
    fail-safe collapse: ambiguity mints nothing, the over-block direction). One
    expression, FOUR consumers (this mint surface, the read bind, the #1083
    flag-scan window, the retirement observer below) — mint==read by construction,
    which is the reduction-theorem premise: a quoted cd-prefix single-destructive
    compound mints exactly the token its BARE destructive leg already minted on
    certified bytes. Regions themselves stay WHOLE (GAP5 compound-refuse and the
    GAP1 danger check consume the region, not this surface — the two-gates-one-value
    coupling is deliberate and cert-pinned)."""
    return (
        _single_destructive_leg(region)
        or _single_detectable_leg(region)
        or region
    )


def _collect_pairs(texts: list) -> dict:
    """Map every COMPLETE (op_type, target) pair found across `texts` to the
    command region that produced it (locate_command_regions + extract_command_context
    over the read-symmetric _extraction_surface + _target_value). A region with an op
    but no target contributes no pair. The PAIR still maps to the WHOLE region (the
    leg surface feeds only the extraction — anchoring/multiplicity/GAP1 see the region)."""
    pairs: dict = {}
    for text in texts:
        for region in locate_command_regions(text):
            cmd_ctx = extract_command_context(_extraction_surface(region))
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
    "not_dangerous": "the selected command is not one that the merge guard gates — it runs without a token, so no authorization is needed",
    "compound_command": "the selected option chained multiple commands with a shell command separator — approve one command at a time",
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


def _leg_bounded_flag_scan_surface(flag_scan_text: str, the_command: str) -> str:
    """#1083: bound the #1042 privileged-flag scan to the destructive command's own
    LEG within the option text. Anchors at the minted command's region, then runs
    the SAME two-tier leg selection the read bind seam runs over the anchored
    suffix — unique dangerous leg, else unique detectable leg (the emergent-danger
    case, where the op's leg is not dangerous in isolation), else the_command.
    Reusing _single_destructive_leg/_single_detectable_leg (not a reimplementation)
    makes mint and read converge on the op's OWN leg BY CONSTRUCTION for every
    compound shape — op-first (`close 42 && echo X`), op-later (`echo X && close
    42`), and op-later-with-real-flag (`cd /repo && gh pr merge 42 --admin`, where
    a positional leg[0] window would DROP the op's own flag and over-block the
    faithful click). Wider than the region (keeps the #1042 truncation-recovery
    property: a flag after a quoted argument, same leg, still binds); narrower
    than the full text (a flag literal in a benign continuation leg or on a later
    line no longer binds — that full-text bind both DENIED the byte-identical
    faithful re-approval, an over-block, and could authorize an ESCALATED
    single-command execution approved as plain, a laundering channel). Fallback on
    a failed anchor OR an ambiguous suffix (no unique dangerous/detectable leg) is
    the_command alone — fail-toward-over-block (mint may bind fewer flags -> set
    mismatch -> DENY), NEVER a wider surface. The selection runs on the ANCHORED
    suffix, never the raw option text: option text is PROSE+command, where
    newline/semicolon carry prose meanings and prose legs are not commands —
    anchoring AT the command scopes leg semantics to the command's own span."""
    idx = flag_scan_text.find(the_command)
    if idx == -1:
        return the_command
    suffix = flag_scan_text[idx:]
    return (
        _single_destructive_leg(suffix)
        or _single_detectable_leg(suffix)
        or the_command
    )


def _mint_context_from_bundle(questions: list, answers: dict) -> MintResult:
    """The token a click mints: the one `_mint_context_by_reading` binds, unless the
    clicked option shows exactly one command, in a code span, that the pre-hook refuses
    as a single operation and would not run under that token (or there is none). That
    command binds its exact text, so the approval runs it, and only it. Every refusal
    other than finding no command stands."""
    result = _mint_context_by_reading(questions, answers)
    if result.refusal_reason not in (None, "no_command"):
        return result
    command = _clicked_refused_command(questions, answers)
    if command is None or (
        result.context is not None and _token_matches_command({"context": result.context}, command)
    ):
        return result
    operation = extract_command_context(
        _single_destructive_leg(command) or _single_detectable_leg(command) or command
    ).get("operation_type") or detect_command_operation_type(command) or "guarded command"
    return MintResult({"operation_type": operation, "command_text": normalize_command_text(command)}, None)


def _clicked_refused_command(questions: list, answers: dict) -> str | None:
    """The one command the clicked options show in code spans that the pre-hook refuses
    as a single operation, or None when they show none or several."""
    found: dict[str, str] = {}
    for q in questions:
        if not isinstance(q, dict) or q.get("multiSelect", False):
            continue
        text = _selected_option_text(q.get("options"), answers.get(q.get("question", "")))
        for _raw, content in _code_spans(text or ""):
            span = content.strip()
            if span and is_dangerous_command(span):
                found[normalize_command_text(span)] = span
    if len(found) != 1:
        return None
    command = next(iter(found.values()))
    return None if is_compound_destructive_command(command) else command


def _mint_context_by_reading(questions: list, answers: dict) -> MintResult:
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
      5. extract_command_context(_extraction_surface(the single distinct command))
         → the mint context, binding op/target (#1031/#1032) + privileged flags
         (#1042 set-equality). The extraction consumes the read-symmetric
         three-tier LEG surface (#1195 OBS-H) so a quoted single-destructive
         compound mints from its destructive leg; the danger/compound gates and
         the pair mapping still see the WHOLE region.

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
    option carries no command → fails anchoring → no mint. NOT a residual —
    post-click escalation/substitution is bound by TWO structural guarantees:
    (1) is_dangerous WRITE-GATE — the mint writes a token ONLY for a command the READ
    floor gates (is_dangerous_command, the shared SSOT), so mint ⊆ read BY
    CONSTRUCTION: a token is never minted for a command the read side would run
    WITHOUT one (a bare reversible close, a non-mutating API call), and a compound
    option mints nothing (is_compound_destructive_command, mirroring the read's
    compound rejection exactly). (2) the #1042 set-equality bind REFUSES an exec-time
    changed (op,target) OR an exec-time extra privileged flag in the op-class
    PRIVILEGED_FLAGS denylist — push→force and close→delete-branch escalations are
    covered (a plain push and a force push mint DISTINCT ops; close binds
    --delete-branch). The denylist is the security-owned membership surface.
    """
    # The operator's ACTION SURFACE = the CLICKED option(s); free-text never mints.
    selected_option_texts: list[str] = []

    # EXPLICIT (shape b): a bundle with NO options anywhere has no clicked option
    # to anchor on → it can NEVER mint. Minting from question prose or a typed
    # free-text answer would make auth depend on un-clicked text — a forbidden
    # under-block class. AUQ structurally carries 2-4 options per question, so this
    # is fail-closed defense for a theoretical/replayed no-options payload. The
    # per-question decline/defer veto still runs below for mixed bundles.
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

    # ── GAP5: compound-REFUSE (PRE-extract). Run is_compound_destructive_command on
    # the EXTRACTED command REGIONS (the doc's "command surface"), NOT the raw option
    # text — so a faithful single command whose surrounding PROSE / multi-line
    # description happens to contain a separator (a newline, `;`, `|`, `&&`) is NOT
    # falsely refused (scanning the raw text re-created the #1049/#1052 FP class). A
    # QUOTED/backtick compound comes back from locate_command_regions as ONE region with
    # the separator INTACT (`"gh pr merge 5 && rm -rf /"`) → is_compound=True → REFUSE
    # here. A BARE compound is different: _BARE_COMMAND_RE stops at the first separator,
    # so an unquoted `gh pr merge 5 && rm -rf /` TRUNCATES to the region `gh pr merge 5`
    # and GAP5 does NOT fire on it — the load-bearing backstop for the bare case is the
    # read side's is_compound_destructive_command refuse (merge_guard_pre.py, checked
    # BEFORE the token lookup), which denies the full command regardless. Either way the
    # chimeric/ride-along compound STAYS closed (it has ONE recognized pair but must not
    # mint — split+multiplicity would still mint it; this explicit refuse does not, and
    # the read side independently refuses the bare form).
    # Mirrors the read side's is_compound rejection EXACTLY (shared SSOT). ──
    for _option_text in selected_option_texts:
        for _region in locate_command_regions(_option_text):
            if is_compound_destructive_command(_region):
                return MintResult(None, "compound_command")

    # A guarded command written only as data inside a backticked command the pre-hook
    # allows (the heredoc body of `gh issue create --body-file -`, the message of
    # `git commit -F -`) is not a command the approval runs, so it binds nothing.
    clicked_texts = selected_option_texts
    selected_option_texts = [_without_allowed_spans(t) for t in selected_option_texts]
    # A clicked option that quotes a guarded command in a code span (the spans
    # left after the line above) names its command there; its prose binds
    # nothing, so a guarded command or a flag it mentions outside the span is
    # neither a second operation nor part of the binding.
    quoted = [
        [raw for raw, content in _code_spans(t) if content.strip()]
        for t in selected_option_texts
    ]
    if any(quoted):
        selected_option_texts = ["\n".join(raws) for raws in quoted if raws]

    # ── Step 3: distinct-(op,target) multiplicity over the CLICKED options ONLY
    # (pure-floor scope — the question prose is NOT cross-checked): ==1 mints, >1
    # refuses (a single click carrying 2 commands), ==0 yields no token. ──
    bundle_pairs = _collect_pairs(selected_option_texts)
    # An interpreter program is read by the pre-hook's own interpreter reader and
    # bound by its text, whether its guarded command is split argv the region finder
    # cannot see (`subprocess.run(["gh", "pr", "merge", "5"])`) or a string it can
    # (`os.system("gh pr merge 5 --admin")`, whose quoted flag the region reading
    # misses). A located command with another (op, target) beside it is a second
    # operation.
    exact, exact_spans = _exact_text_mint_context(selected_option_texts)
    if exact.refusal_reason != "no_command":
        ctx = exact.context
        beside = _collect_pairs([_without(t, exact_spans) for t in selected_option_texts])
        if ctx is not None and any(
            pair != (ctx.get("operation_type"), _target_value(ctx)) for pair in beside
        ):
            return MintResult(None, "multiple_commands")
        return exact
    if not bundle_pairs and _collect_pairs(clicked_texts):
        # The only guarded command was data in a command the pre-hook allows.
        return MintResult(None, "not_dangerous")
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

    # ── GAP1: is_dangerous WRITE-GATE. Mint a token ONLY for a command the READ floor
    # would gate (is_dangerous_command — the SAME shared predicate, so mint⊆read by
    # construction). A detected-but-NOT-dangerous command — a bare reversible
    # `gh pr close 5`, a non-mutating API call, or prose the classifier loosely typed
    # — mints NO token: the read side ALLOWS it without a token, so withholding one is
    # not an over-block (the command runs regardless). Closes the bare-close
    # escalation + the prose-API mint + every detected-but-not-dangerous case. ──
    if not is_dangerous_command(the_command):
        return MintResult(None, "not_dangerous")

    # ── Step 5: extract the single distinct command's context to mint. Op/target
    # are derived from `the_command` (region-anchored). The privileged-flag scan
    # (#1042) runs over the command's LEG-BOUNDED window within the option text
    # (#1083, _leg_bounded_flag_scan_surface): wider than the bare-command region,
    # so a flag after a quoted argument is not lost to truncation, but bounded at
    # the command's own leg so a flag literal in a benign continuation leg no
    # longer binds (that full-text bind denied the faithful byte-identical
    # re-approval AND could authorize an escalated execution approved as plain).
    # #1059: the wrapping backtick/curly delimiters are space-stripped FIRST
    # (_strip_command_wrapper, BEFORE the window's find-anchor, so the anchor sees
    # the same spacing the region parser produced — order is load-bearing) — the
    # #1042 set-equality stays EXACT, so an exec-time EXTRA flag still REFUSES. ──
    ctx = extract_command_context(
        _extraction_surface(the_command),
        flag_scan_text=_leg_bounded_flag_scan_surface(
            _strip_command_wrapper(" ".join(selected_option_texts)), the_command
        ),
    )
    if _DRY_RUN_RE.search(the_command) or _AUTO_RE.search(the_command):
        # A dry run or an --auto merge is a different operation from the real one
        # the (op, target) token would also run, so the approval binds the text of
        # the code span that holds it. Unquoted prose has no span to bound the
        # command, so a prose approval keeps its target binding.
        spans = [
            content.strip()
            for text in selected_option_texts
            for _raw, content in _code_spans(text)
            if is_dangerous_command(content.strip())
        ]
        if len(spans) == 1:
            ctx = {**ctx, "command_text": normalize_command_text(spans[0])}
    return MintResult(ctx, None)


_FENCED_BLOCK_RE = re.compile(r"```[^\n`]*\n(.*?)```", re.DOTALL)
_BACKTICK_RUN_RE = re.compile(r"`+")


def _ends_span(text: str, after: int) -> bool:
    """Whether a backtick ending just before `after` can close a code span: it ends
    the text or is followed by whitespace or punctuation, where a backtick opening a
    substitution is followed by the command it runs."""
    return after == len(text) or text[after].isspace() or text[after] in ".,;:!?)]}'\""


def _substitution_span_end(text: str, early: int) -> int | None:
    """The index of the backtick that closes a single-backtick code span whose
    closer at `early` really opens a substitution (`origin `git branch``,
    `x=`date``, `--body x`date``); None when the backticks after it do not read as
    substitutions. Each substitution's closer follows a non-space character (a
    backtick after a space opens a new span instead, as in prose), and the span's
    closer is the first backtick after a substitution that is followed by
    whitespace, punctuation or the end."""
    ticks = [k for k in range(early, len(text)) if text[k] == "`"]
    t = 0
    while True:
        if t + 1 >= len(ticks) or text[ticks[t + 1] - 1].isspace():
            return None
        if t + 2 >= len(ticks):
            return None
        nxt = ticks[t + 2]
        if _ends_span(text, nxt + 1):
            return nxt
        t += 2


def _code_spans(text: str) -> list[tuple[str, str]]:
    """(raw, content) for each fenced block and inline code span in `text`. An inline
    span opens with a run of backticks and closes at the next run of the same length
    (`` cmd with `x` `` holds backticks). A single-backtick span whose closer is
    followed by a word was closed early by a backtick opening a substitution: it
    runs on past each substitution to the backtick that closes it (see
    _substitution_span_end). A span of n > 1 backticks with no later run of n closes
    at the first longer run, whose extra leading backticks are the command's own
    (``cmd `x```). Inline spans may run over lines."""
    spans = [(m.group(0), m.group(1)) for m in _FENCED_BLOCK_RE.finditer(text)]
    rest = _FENCED_BLOCK_RE.sub(lambda m: " " * len(m.group(0)), text)
    runs = list(_BACKTICK_RUN_RE.finditer(rest))
    i = 0
    while i < len(runs):
        opener = runs[i]
        width = len(opener.group(0))
        close = next((j for j in range(i + 1, len(runs)) if len(runs[j].group(0)) == width), None)
        if close is None and width > 1:
            close = next((j for j in range(i + 1, len(runs)) if len(runs[j].group(0)) > width), None)
        if close is None:
            i += 1
            continue
        content = rest[opener.end():runs[close].end() - width]
        end = runs[close].end()
        if len(opener.group(0)) == 1 and not _ends_span(rest, end):
            last = _substitution_span_end(rest, runs[close].start())
            if last is not None:
                content, end = rest[opener.end():last], last + 1
        spans.append((text[opener.start():end], content))
        i = next((k for k in range(close + 1, len(runs)) if runs[k].start() >= end), len(runs))
    return spans


# Ops the pre-hook binds only by exact text: a branch-merge's target is its base, and
# its head is not part of the target, so an (op, target) token would run any head.
_TEXT_BOUND_OPS = frozenset({"branch-merge"})


def _mask_substitutions(text: str) -> str | None:
    """`text` with each command substitution (`$(…)`, a backtick span) replaced by a
    placeholder word, innermost first; None when one cannot be closed."""
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\$\([^()]*\)|`[^`]*`", "_", text)
    return None if "$(" in text or "`" in text else text


def _guarded_substitution_count(text: str) -> int:
    """How many command substitutions in `text` hold a guarded command, innermost
    first, the way _mask_substitutions walks them."""
    count, previous = 0, None
    while previous != text:
        previous = text
        count += sum(is_dangerous_command(m.group(1) or m.group(2) or "")
                     for m in re.finditer(r"\$\(([^()]*)\)|`([^`]*)`", text))
        text = re.sub(r"\$\([^()]*\)|`[^`]*`", "_", text)
    return count


# The op an approval records for a command that picks one of several guarded commands;
# it has no success line of its own, so a successful run retires it.
_ALTERNATIVE_BRANCHES_OP = "choice between guarded commands"


# `NAME="text"; eval "$NAME"`: an eval of a variable assigned the text just before.
_ASSIGNED_EVAL_RE = re.compile(
    r"""^\s*(?P<name>[A-Za-z_]\w*)=(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)')\s*;\s*"""
    r"""eval\s+"?\$(?:\{(?P=name)\}|(?P=name)\b)"?\s*$"""
)


_LITERAL_ASSIGNMENT_RE = re.compile(r"""^([A-Za-z_]\w*)=("[^"`\\]*"|'[^']*'|[^\s;&|$`'"\\()]*)$""")
_VARIABLE_REF_RE = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")


def _resolved_evals(command: str) -> str:
    """`command` with each eval of a single string whose variables earlier legs all
    assign literal values replaced by the eval of the text those values give, so
    `PR=5; c="gh pr merge $PR --squash"; eval "$c"` reads as `PR=5; c=...; eval
    'gh pr merge 5 --squash'`. Only `;` and newlines may join the legs up to the
    eval, so every assignment runs before it. Unchanged otherwise."""
    if "eval" not in command or "<<" in command:
        return command
    split = _top_level_legs(command)
    if split is None:
        return command
    legs, seps = split
    env: dict[str, str] = {}

    def expand(text: str) -> str | None:
        names = [braced or bare for braced, bare in _VARIABLE_REF_RE.findall(text)]
        if any(name not in env for name in names):
            return None
        return _VARIABLE_REF_RE.sub(lambda m: env[m.group(1) or m.group(2)], text)

    changed = False
    for k, leg in enumerate(legs):
        if k and seps[k - 1] not in (";", "\n"):
            break
        word = leg.strip()
        m = _LITERAL_ASSIGNMENT_RE.match(word)
        if m:
            name, value = m.groups()
            text = value[1:-1] if value[:1] == "'" else expand(value[1:-1] if value[:1] == '"' else value)
            if text is None:
                env.pop(name, None)
            else:
                env[name] = text
            continue
        arg = word[4:].strip() if word.startswith(("eval ", "eval\t")) else ""
        # eval parses its string again, so a single-quoted one expands too.
        if arg[:1] in ("'", '"') and arg.endswith(arg[0]) and len(arg) > 1 and arg[0] not in arg[1:-1]:
            text = expand(arg[1:-1])
        elif arg and not any(ch in arg for ch in " \t'\"\\"):
            text = expand(arg)
        else:
            text = None
        if text is not None and text != arg:
            legs[k] = leg[: len(leg) - len(leg.lstrip())] + "eval " + shlex.quote(text)
            changed = True
    if not changed:
        return command
    return legs[0] + "".join(sep + leg for sep, leg in zip(seps, legs[1:]))


def _merge_reading(command: str) -> str:
    """`command` as the guard reads the merge it runs: an eval of literal values
    resolved by _resolved_evals, otherwise the classifier's own rewrite (a literal
    variable expanded), otherwise the command itself."""
    resolved = _resolved_evals(command)
    if resolved != command:
        return resolved
    return rewritten_executed_command(command) or command


def _target_is_a_variable(ctx: dict) -> bool:
    """Whether the region reading's target is a shell variable (`gh pr merge "$PR"`,
    `PR=5; gh pr merge $PR`). The same text runs another target once the command
    assigns the variable another value, so only the command's text can bind it."""
    target = _target_value(ctx)
    return isinstance(target, str) and "$" in target


def _implicit_target(ctx: dict) -> bool:
    """Whether the region reading's target is the current branch the op leaves
    implicit (`gh pr merge --squash`, `git push --force`)."""
    target = _target_value(ctx)
    return isinstance(target, str) and "\x00" in target


def _evald_target(span: str) -> str | None:
    """The target of the one guarded command `span` evals as a single string (`eval
    "gh pr close 5 --delete-branch"`, or a variable assigned it just before): its
    literal target, or a run-time one (a variable, the current branch it leaves
    implicit). None when `span` is not such an eval or its command names no
    target."""
    m = _ASSIGNED_EVAL_RE.match(span)
    if m:
        text = m.group("dq") if m.group("dq") is not None else m.group("sq")
    else:
        try:
            words = shlex.split(span)
        except ValueError:
            return None
        if len(words) != 2 or words[0] != "eval":
            return None
        text = words[1]
    target = _target_value(extract_command_context(text))
    return target if isinstance(target, str) else None


def _inner_flags_differ(leg: str, inner: str) -> bool:
    """Whether the command a shell's -c string runs carries other flags than the outer
    reading of its leg sees (`bash -c 'gh pr merge 5 --admin'` reads no --admin from
    outside). The pre-hook holds a token to both readings, so such a command can only
    be bound by its text."""
    outer = extract_command_context(leg)
    run = extract_command_context(_single_destructive_leg(inner) or _single_detectable_leg(inner) or inner)
    return set(outer.get("bound_flags", [])) != set(run.get("bound_flags", []))


def _exact_text_reading(span: str, located: str) -> dict | None:
    """The (op, target, flags) of a backticked command the region reading cannot
    bind, read the way the pre-hook reads it; None when the region reading binds
    it where the option shows it (`located`, the span with its backticks) or no
    single guarded op is found. The shapes:
      - an interpreter program (`python3 -c 'subprocess.run(["gh", "pr", "merge",
        "5"])'`), read by the interpreter reader, even where its merge text is
        contiguous (a quoted flag the region reading misses is then bound);
      - a command whose target is a substitution (`git push --force origin
        $(git branch --show-current)`): its op is read with the substitutions
        masked, so a substitution that itself runs guarded commands types nothing;
      - a guarded op in a shell's -c string that the region reading locates nothing
        in (`bash -c 'git push --force origin feat'`), read from that string, in the
        command's one destructive leg when other legs surround it;
      - a plain command whose target the pre-hook binds only by text (a
        branch-merge, a GraphQL pull-request mutation);
      - an eval of a heredoc the classifier refuses because it cannot read the
        command it runs (`${N:-5}`, a substitution): bound by its text alone;
      - an if/elif/else or case whose branches run different guarded commands:
        bound by its text alone;
      - an eval of a single string the region reading binds nothing in, whose
        command names a target (`eval "gh pr merge $PR"`, `eval "gh pr merge
        --squash"`, `eval "gh pr close 5 --delete-branch"`): bound by its text alone;
      - a command the classifier judges after a rewrite (an eval of readable text,
        a file written then run, a literal variable, a script given a whole
        command) where the region reading binds nothing, read from the command
        the classifier reports it runs.
    Any other plain command is left to the region reading, so its own extraction
    stays the only binding for those."""
    ctx = interpreter_command_context(span)
    if ctx and ctx.get("operation_type"):
        return ctx
    if unreadable_evald_heredoc(span):
        # The classifier cannot read what this eval runs, so nothing but its text
        # can bind it: the token runs exactly the command the user saw.
        return {"operation_type": "heredoc eval"}
    if alternative_destructive_branch_count(span) >= 2:
        # Each branch runs its own guarded command, so no one (op, target) stands for
        # the command: its text binds it, and a branch runs only as part of it.
        return {"operation_type": _ALTERNATIVE_BRANCHES_OP}
    pairs = _collect_pairs([located])
    leg = _single_destructive_leg(span) or span
    # The region reading binds only a command the pre-hook can type the same way; a
    # pair found in a written file's text (`cat > f <<EOF … EOF; bash f`) cannot be.
    region_binds = bool(pairs) and bool(
        extract_command_context(_single_destructive_leg(span) or _single_detectable_leg(span) or span)
        .get("operation_type")
    )
    rewritten = None if region_binds else rewritten_executed_command(span)
    if rewritten is not None:
        # A file the command writes and then runs is read as the program it holds.
        ctx = interpreter_command_context(rewritten)
        if ctx and ctx.get("operation_type"):
            return ctx
        if is_compound_destructive_command(rewritten):
            return None
        body = rewritten
    # A `\$(` or `\`` escaped in the calling shell's double quotes is the -c string's
    # or eval's own substitution, read with the string below.
    elif _has_command_substitution(re.sub(r"\\[$`]", "", span)):
        body = _mask_substitutions(leg)
        if body is None:
            return None
        if not extract_command_context(
            _single_destructive_leg(body) or _single_detectable_leg(body) or body
        ).get("operation_type"):
            # The guarded command is the substitution itself (`x.py "`gh pr merge 5`"`):
            # masking removes it, so the classifier's reading of the command stands.
            # Two guarded substitutions are two operations, which no approval runs.
            if _guarded_substitution_count(leg) != 1:
                return None
            body = leg
    else:
        inner = shell_c_string(leg)
        if inner is not None:
            # A -c string that exports GH_REPO, GH_HOST or GH_CONFIG_DIR, or changes
            # repository, before its merge or close binds by text, as the same command
            # run directly does.
            if is_compound_destructive_command(inner) or (
                pairs and not _inner_flags_differ(leg, inner) and not exported_gh_target_before_guarded_leg(leg)
                and not target_changes_before_guarded_leg(leg, branch=False)
            ):
                return None
            body = inner
        else:
            body = leg
    ctx = extract_command_context(
        _single_destructive_leg(body) or _single_detectable_leg(body) or body
    )
    plain = body is leg and rewritten is None and not _has_command_substitution(span)
    if not ctx.get("operation_type"):
        return None
    if plain and not (
        ctx["operation_type"] in _TEXT_BOUND_OPS
        or ctx.get("pr_node_id")
        or "graphql_mutation" in ctx
        # An eval of a string the region reading binds nothing in (a target read at
        # run time, or a command it does not locate inside the quotes) has no
        # (op, target) it can see; its text binds it, so the approval runs exactly
        # the command shown.
        or (not pairs and _evald_target(span) is not None)
        # A variable target (`gh pr merge "$PR"`, `PR=5; gh pr merge $PR`) binds by
        # text, so the command with another value assigned is not approved.
        or _target_is_a_variable(ctx)
        # An op on the current branch after a checkout or cd (`git checkout feat/y &&
        # gh pr merge --squash`) binds by text, so another branch's is not approved.
        or (_implicit_target(ctx) and target_changes_before_guarded_leg(span))
        # A close of the current branch's PR (`gh pr close --delete-branch`) names
        # no number the extractor reads, so no (op, target) binds it; its text does.
        or (ctx["operation_type"] == "close" and _target_value(ctx) is None)
        # A merge or close run after `export GH_REPO=…` (or GH_HOST, GH_CONFIG_DIR)
        # acts in that repository, which the op's own reading does not see; its text
        # binds it.
        or (ctx["operation_type"] in ("merge", "close") and exported_gh_target_before_guarded_leg(span))
        # So does an op on a named PR or branch run after a change of directory or
        # repository (`cd ../other && gh pr merge 5`, `git -C ../other branch -D
        # feat/x`), whose target names one of that repository.
        or (ctx["operation_type"] in REPO_RELATIVE_OPS and target_changes_before_guarded_leg(span, branch=False))
        # A quoted value of a bound flag (`--repo="o/b"`) reads as a placeholder that
        # every value shares; its text binds it.
        or binds_a_masked_value(ctx)
    ):
        return None
    return ctx


def _exact_text_mint_context(option_texts: list[str]) -> tuple[MintResult, list[str]]:
    """The mint for a clicked option's backticked command that _exact_text_reading
    reads, and the raw spans it read. The token carries the command's normalized
    text, which is all the pre-hook then matches: the approval runs that command and
    no other. One distinct command per approval; a compound never mints."""
    found: dict[str, dict] = {}
    spans: list[str] = []
    for text in option_texts:
        for raw, content in _code_spans(text):
            span = content.strip()
            if not span or not is_dangerous_command(span):
                continue
            rewritten = rewritten_executed_command(span)
            if is_compound_destructive_command(span) or (
                rewritten is not None and is_compound_destructive_command(rewritten)
            ):
                # One approval runs one guarded operation, also when the command
                # carries several in a value it hands to something that runs it.
                return MintResult(None, "compound_command"), []
            ctx = _exact_text_reading(span, raw)
            if ctx:
                found[normalize_command_text(span)] = ctx
                spans.append(raw)
    if len(found) != 1:
        return MintResult(None, "multiple_commands" if found else "no_command"), spans
    command_text, ctx = next(iter(found.items()))
    return MintResult({**ctx, "command_text": command_text}, None), spans


def _without(text: str, spans: list[str]) -> str:
    for span in spans:
        text = text.replace(span, " ")
    return text


def _without_allowed_spans(text: str) -> str:
    """`text` without each code span whose command the pre-hook allows; a guarded
    command such a span holds is data it does not run."""
    return _without(text, [raw for raw, content in _code_spans(text)
                           if content.strip() and not is_dangerous_command(content.strip())])


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
        "PACT merge-guard: no approval was recorded from this question — " + why + ". "
        "If it was meant to approve a guarded command, ask again as a single-select "
        "question whose approving option has a short label and the command in "
        "backticks in its description, for example Run `gh pr merge <N>` to merge "
        "this PR (the merge checkpoint in peer-review.md). If it was not an "
        "approval, ignore this note."
    )


# Target values carry NUL-framed sentinels for targets a command leaves implicit
# (the current branch, the default remote), and multi-item targets are netstring
# encoded (`<len>:<item>...`). The confirmation renders both readably.
_SENTINEL_WORDS = (
    ("\x00implicit-merge", "the current branch"),
    ("\x00implicit-force-push", "the current branch"),
    ("\x00implicit", "the default remote"),
)
_CONFIRMATION_TARGET_MAX = 200
# The context keys whose values are netstring-encoded multi-item targets.
_NETSTRING_TARGET_KEYS = ("branch_set", "push_set", "force_push_set", "mass_target")


def _split_netstring(value: str) -> list[str] | None:
    """The items of a `<len>:<item>...` encoding, or None when the value is not one."""
    items, i = [], 0
    while i < len(value):
        colon = value.find(":", i)
        length = value[i:colon]
        # isdigit() alone accepts non-ASCII digits ("²") that int() rejects.
        if colon <= i or not (length.isascii() and length.isdigit()):
            return None
        end = colon + 1 + int(length)
        if end > len(value):
            return None
        items.append(value[colon + 1:end])
        i = end
    return items or None


def _readable_item(item: str) -> str:
    for sentinel, words in _SENTINEL_WORDS:
        if item.startswith(sentinel):
            rest = item[len(sentinel):]
            parts = _split_netstring(rest) if rest else []
            if parts is None:
                parts = [rest]
            return ", ".join([words] + [_readable_item(p) for p in parts])
    return item


def _plain(text: str) -> str:
    """`text` with every control character (NUL included) dropped, capped in length."""
    text = "".join(ch for ch in text if ch.isprintable())
    if len(text) > _CONFIRMATION_TARGET_MAX:
        text = text[:_CONFIRMATION_TARGET_MAX] + "..."
    return text


def _readable_target(value: str, is_netstring: bool) -> str:
    """A minted target value as plain text: sentinels named, netstrings split, and
    every control character (NUL included) dropped, capped in length."""
    parts = (_split_netstring(value) if is_netstring else None) or [value]
    return _plain(", ".join(_readable_item(p) for p in parts))


def _mint_confirmation(context: dict) -> str:
    """The notice printed after a successful mint, naming what the approval
    authorizes, so a click that was not an approval of this command is caught
    before the command runs. The bound flags (`--admin`, `--repo=…`,
    `--delete-branch`, …) are part of what the token authorizes, so every one is
    named."""
    op = context.get("operation_type") or "destructive command"
    target = _target_value(context)
    is_netstring = any(
        target is not None and context.get(k) == target for k in _NETSTRING_TARGET_KEYS
    )
    if (
        isinstance(target, str) and target == context.get("pr_number")
        and target.isascii() and target.isdigit()
    ):
        where = f" on PR #{target}"
    elif "command_text" in context:
        where = ""  # an exact-command token binds its text, not a readable target
    elif isinstance(target, str):
        where = " on " + _readable_target(target, is_netstring)
    else:
        where = ""
    flags = context.get("bound_flags")
    if isinstance(flags, list) and flags:
        where += " with " + _plain(" ".join(str(f) for f in flags))
    return (
        f"PACT merge-guard: an approval was recorded for a {op}{where}. It "
        f"authorizes that command for {TOKEN_TTL // 60} minutes, at most {MAX_USES} "
        "times. If the option the user selected was not an approval of this "
        "command, do not run it."
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

    # A random suffix makes every token's name its own, so two tokens minted in one
    # second never share a name, nor do their `.consumed` records.
    token_path = token_dir / f"merge-authorized-{timestamp}-{secrets.token_hex(4)}"

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
        # Extremely unlikely collision — try once more with a fresh suffix
        token_path = token_dir / f"merge-authorized-{timestamp}-{secrets.token_hex(4)}"
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


_CONFIRMING_STATE = {"merge": "MERGED", "close": "CLOSED"}

# gh resolves the PR number against the repo of its working directory and its
# GH_REPO / GH_HOST / GIT_DIR environment. A command that changes either can name
# a PR in a different repo from the hook's own, so the state check would read the
# wrong PR. Deliberately wide: a false match only keeps the token, today's
# behaviour. One shape is followed instead of doubted: a single `cd <literal
# absolute dir> &&` before the gh leg, where the check runs in that directory.
_REPO_REDIRECT_RE = re.compile(
    r"(?<![\w./-])pushd(?![\w./-])"
    r"|\bgit\b[^;&|\n]*\s-C"
    r"|(?<![\w])(?:GH_REPO|GH_HOST|GIT_DIR)="
    r"|\bgh\s+repo\s+set-default\b"
)
_CD_WORD_RE = re.compile(r"(?<![\w./-])cd(?![\w./-])")
# `&&` only: after `cd x;` or `cd x ||` the gh leg can run in the old directory.
# The path class excludes every expansion and quoting character ($ ~ globs braces `).
_LITERAL_CD_RE = re.compile(
    r"(?<![\w./-])cd[ \t]+(/[^\s;&|<>(){}$~*?\[\]`'\"\\]*)[ \t]*&&"
)
_GH_PR_OP_RE = re.compile(r"\bgh\s+pr\s+(?:merge|close)\b")


def _state_check_dir(
    command: str, frame_cwd: object = None
) -> tuple[bool, str | None]:
    """Where the PR state check may run for this command: (True, <dir>) for the
    session's directory (the hook frame's `cwd`, when it is an absolute existing
    directory), (True, None) for the hook's own directory when the frame names
    none, (True, <dir>) after a single literal absolute `cd`, and (False, None)
    when the command may target another repo."""
    if _REPO_REDIRECT_RE.search(command):
        return False, None
    cd_count = len(_CD_WORD_RE.findall(command))
    if cd_count == 0:
        # The command ran in the session's shell directory, which an earlier
        # Bash call may have changed; the hook's own directory may differ.
        if (
            isinstance(frame_cwd, str)
            and os.path.isabs(frame_cwd)
            and os.path.isdir(frame_cwd)
        ):
            return True, frame_cwd
        return True, None
    literal = _LITERAL_CD_RE.search(command)
    gh_leg = _GH_PR_OP_RE.search(command)
    if cd_count != 1 or literal is None or gh_leg is None:
        return False, None
    path = literal.group(1)
    if gh_leg.start() < literal.end() or ".." in path.split("/"):
        return False, None
    if not os.path.isdir(path):
        return False, None
    return True, path


# A heredoc's body and closing marker, after the line that opens it.
_HEREDOC_TAIL_RE = re.compile(
    r"(<<-?[ \t]*['\"]?(\w+)['\"]?[^\n]*)\n(?:.*?\n)?\t*\2(?!\w)", re.DOTALL
)


def _top_level_legs(command: str, groups: bool = False) -> tuple[list[str], list[str]] | None:
    """The legs of `command` and the list operators between them (`;`, newline,
    `&&`, `||`, `|`, a background `&`), outside quotes. None when the command opens
    a group or a substitution (`(`, `{`, `$(`, a backtick) or leaves a quote open.
    With `groups`, a balanced subshell, brace group or substitution is part of the
    leg it is in instead. A heredoc's body is part of the leg that opens it, not
    later legs."""
    command = _HEREDOC_TAIL_RE.sub(r"\1", command)
    legs, seps, start, i, quote = [], [], 0, 0, ""
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch == "\\":
            i += 2
            continue
        elif ch in "'\"":
            quote = ch
        elif groups and ch in "({`":
            end = _group_end(command, i)
            if end is None:
                return None
            i = end + 1
            continue
        elif ch in "(){}`":
            return None
        elif ch in ";\n|&":
            pair = command[i:i + 2]
            redirect = ch == "&" and (
                command[i - 1:i] in (">", "<") or command[i + 1:i + 2] == ">"
            )
            if not redirect:
                op = pair if pair in ("&&", "||") else ch
                legs.append(command[start:i])
                seps.append(op)
                i += len(op)
                start = i
                continue
        i += 1
    if quote:
        return None
    legs.append(command[start:])
    return legs, seps


def _group_end(command: str, start: int) -> int | None:
    """The index of the `)`, `}` or backtick that closes the group, substitution or
    backtick span opened at `start`, outside quotes; None when it is not closed."""
    opener = command[start]
    if opener == "`":
        i = start + 1
        while i < len(command):
            if command[i] == "\\":
                i += 2
                continue
            if command[i] == "`":
                return i
            i += 1
        return None
    closer = ")" if opener == "(" else "}"
    depth, quote, i = 0, "", start
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch == "\\":
            i += 2
            continue
        elif ch in "'\"":
            quote = ch
        elif ch == "`":
            end = _group_end(command, i)
            if end is None:
                return None
            i = end + 1
            continue
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


# --dry-run, or -n alone or bundled with git push's other flag-only short options
# (`-fn`); a word like `-name` is not one.
_DRY_RUN_RE = re.compile(r"(?<!\S)(?:--dry-run|-[46dfquv]*n[46dfquv]*)(?!\S)")
_AUTO_RE = re.compile(r"(?<!\S)--auto(?!\S)")


def _backgrounded(k: int, legs: list[str], seps: list[str]) -> bool:
    """Whether leg `k` runs in the background past the command's end: it is
    followed by `&`, and the next leg is not a `wait` (or `wait $!`) that waits
    for it."""
    if k >= len(seps) or seps[k] != "&":
        return False
    return k + 1 >= len(legs) or legs[k + 1].split() not in (["wait"], ["wait", "$!"])


def _op_did_not_run(command: str) -> bool:
    """Whether a leg that holds the guarded op is a dry run or runs in the
    background without a `wait` for it, so the command's success says nothing
    about the op. A shell's -c
    string is read the same way, and a string a shell runs from a substitution
    that only prints (`bash -c "$(echo -n '…')"`) is read as the mint reads it,
    so the echo's -n is not the op's dry-run flag."""
    try:
        command = _rewrite_readable_evals(command)
        inner = shell_c_string(command)
        if inner is not None:
            return _op_did_not_run(inner)
        split = _top_level_legs(command)
        if split is None:
            return False
        legs, seps = split
        return any(
            detect_command_operation_type(leg) is not None
            and (_DRY_RUN_RE.search(leg) or _backgrounded(k, legs, seps))
            for k, leg in enumerate(legs)
        )
    except Exception:  # noqa: BLE001 - doubt changes nothing here
        return False


def _group_body(command: str) -> str | None:
    """The body of a command that is one subshell `( … )` or one brace group `{ …; }`,
    whose exit status is its last command's; None for anything else, including a
    group followed by more commands. Quoted text is skipped."""
    text = command.strip()
    if text[:1] == "(":
        opener, closer = "(", ")"
    elif re.match(r"\{\s", text):
        opener, closer = "{", "}"
    else:
        return None
    depth, quote, i = 0, "", 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch == "\\":
            i += 2
            continue
        elif ch in "'\"":
            quote = ch
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                if i != len(text) - 1:
                    return None
                body = text[1:-1].rstrip(" \t")
                if closer == "}":
                    if not body.endswith((";", "\n")):
                        return None
                    body = body[:-1]
                return body
        i += 1
    return None


def _run_succeeded_as_the_op(command: str, op_type: str, tool_response: object) -> bool:
    """Whether this hook firing for `command` means its guarded op succeeded. It is
    read on PostToolUse only, and a failed command reaches PostToolUseFailure, so a
    response in the success shape (`stdout` text, not interrupted) means the
    command exited 0. That is the op's own status when the op runs in the
    foreground as the last leg, unnegated, no `||` before it could have skipped it,
    and it is not a dry run. A shell's -c string, and a command that is one
    subshell or brace group, are read the same way, and a group or substitution
    inside a leg (`(set -a); GH_REPO=o/b gh pr merge 5`, `x=$(…); gh pr merge 5`)
    is part of that leg. Any doubt is False."""
    if not (
        isinstance(tool_response, dict)
        and isinstance(tool_response.get("stdout"), str)
        and tool_response.get("interrupted") is False
    ):
        return False
    try:
        inner = shell_c_string(command)
        if inner is not None:
            return _run_succeeded_as_the_op(inner, op_type, tool_response)
        body = _group_body(command)
        if body is not None:
            return _run_succeeded_as_the_op(body, op_type, tool_response)
        split = _top_level_legs(command, groups=True)
        if split is None:
            return False
        legs, seps = split
        while len(legs) > 1 and not legs[-1].strip() and seps[-1] in (";", "\n"):
            legs.pop()
            seps.pop()
        if "||" in seps or legs[-1].lstrip().startswith("!") or _DRY_RUN_RE.search(legs[-1]):
            return False
        if len(legs) > 1 and _group_body(legs[-1]) is not None:
            return _run_succeeded_as_the_op(legs[-1], op_type, tool_response)
        return detect_command_operation_type(legs[-1]) == op_type
    except Exception:  # noqa: BLE001 - any doubt keeps the token
        return False


def _close_succeeded(command: str, op_type: str | None, tool_response: object) -> bool:
    """Whether a PR close succeeded by the run's own status. Unlike a merge, which
    can exit 0 into a merge queue or with --auto and leave the PR OPEN, a close
    that exits 0 has done its work, so its token retires even when a lagging or
    mismatched state read says otherwise."""
    return op_type == "close" and _run_succeeded_as_the_op(command, op_type, tool_response)


def _git_reports_ref_deleted(command: str, op_type: str, tool_response: object) -> bool:
    """Whether git's own output, in the response's stdout or stderr, reports the
    command's target ref deleted (` - [deleted]   feat/x`), which proves a remote
    ref delete whose exit status is not its own (piped into tail, say). The line
    must name exactly the ref the command deletes."""
    if op_type != "remote-ref-delete" or not isinstance(tool_response, dict):
        return False
    target = extract_command_context(
        _single_destructive_leg(command) or _single_detectable_leg(command) or command
    ).get("target_ref")
    if not isinstance(target, str) or not target:
        return False
    line = re.compile(r"^[ \t]*-[ \t]+\[deleted\][ \t]+" + re.escape(target) + r"[ \t]*$", re.M)
    return any(
        isinstance(tool_response.get(k), str) and line.search(tool_response[k])
        for k in ("stdout", "stderr")
    )


def _pr_state(command: str, frame_cwd: object = None) -> str:
    """The state GitHub reports for the PR the command named (OPEN, MERGED or
    CLOSED), or "" when it cannot be read: any other answer, a URL, branch or implicit target, an -R/--repo target, a directory
    change or repo env assignment other than one literal absolute `cd`, an
    unreachable or slow gh, or any exception. A shell's -c string
    is read as the command it runs, with its positional words in place, so a `cd`
    inside it is followed the same way; a repo env assignment outside it still
    doubts."""
    try:
        inner = shell_c_string(command)
        if inner is not None and not _REPO_REDIRECT_RE.search(command):
            command = inner
        followable, cwd = _state_check_dir(command, frame_cwd)
        if not followable:
            return ""
        ctx = extract_command_context(_extraction_surface(command))
        pr = ctx.get("pr_number")
        if not isinstance(pr, str) or not pr.isdigit():
            return ""
        if any(str(f).startswith("--repo") for f in ctx.get("bound_flags") or []):
            return ""
        from shared.gh_helpers import check_pr_state

        state = check_pr_state(pr, cwd=cwd)
        return state if state in ("OPEN", "MERGED", "CLOSED") else ""
    except Exception:  # noqa: BLE001 - any doubt keeps the token
        return ""


def _token_in_session(token_data: dict, current_session: str) -> bool:
    """Whether a token may be retired from this session.

    SEC-S1 cycle-2: gate ONLY when current_session is populated. When
    current_session=="" (no PACT context), preserve graceful-degradation per
    architect §3 revised design. Cycle-1 fail-OPEN-on-either-empty
    AND-short-circuit WAS itself the attack surface — populated current_session +
    empty token_session let attacker-written tokens through. See
    test_no_session_id_accepts_any_token (in test_merge_guard.py) for the
    preserved invariant; its SEC-S1 inversion counterpart is the fix landing."""
    if not current_session:
        return True
    token_session = token_data.get("session_id", "")
    return bool(token_session) and current_session == token_session


def _retire_exact_text_token(
    command: str,
    frame_cwd: object = None,
    token_dir: Path | None = None,
    stdout_text: object = "",
    tool_response: object = None,
) -> bool:
    """Retire this session's exact-command token on the same success signal Layer 1
    uses for its op: a merge or close of a numbered PR once GitHub reports it in the
    state the command produces, an op with a LAYER1_SUCCESS_STDOUT_PATTERNS entry by
    that output, and any other op (a branch-merge, a merge or close named by a
    GraphQL node ID, an eval of a heredoc the classifier cannot read) by the
    successful run itself, so the approval runs once. The operation classifier
    cannot read such a command, so Layer 1 below never sees it. Any doubt keeps the
    token; the TTL and MAX_USES bound still apply. Never raises."""
    try:
        current_session = get_session_id()
        for path in glob.glob(str((token_dir or TOKEN_DIR) / f"{TOKEN_PREFIX}*")):
            if path.endswith(".consumed") or USE_MARKER_SUFFIX in os.path.basename(path):
                continue
            try:
                with open(path, "r") as f:
                    token_data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            ctx = token_data.get("context") if isinstance(token_data, dict) else None
            if not isinstance(ctx, dict):
                continue
            approved = ctx.get("command_text")
            if not isinstance(approved, str) or not exact_text_matches(
                approved, command, frame_cwd if isinstance(frame_cwd, str) else None
            ):
                continue
            if not _token_in_session(token_data, current_session):
                continue
            op, pr = ctx.get("operation_type"), ctx.get("pr_number")
            if op == "merge" and not (isinstance(pr, str) and pr.isascii() and pr.isdigit()):
                # A merge bound by its text whose PR an earlier leg assigns a literal
                # number (`PR=5; c="gh pr merge $PR"; eval "$c"`, `PR=5; gh pr merge
                # $PR`) follows the merge rule below, as the plain command does.
                resolved = _merge_reading(command)
                named = extract_command_context(
                    _single_destructive_leg(resolved) or _single_detectable_leg(resolved) or resolved
                ).get("pr_number")
                if resolved != command and isinstance(named, str) and named.isascii() and named.isdigit():
                    pr = named
            if op in _CONFIRMING_STATE and isinstance(pr, str) and pr.isascii() and pr.isdigit():
                # The state is read where the command ran: the session's directory, or
                # the one literal absolute `cd` before the op.
                followable, cwd = _state_check_dir(command, frame_cwd)
                if not followable or any(str(f).startswith("--repo") for f in ctx.get("bound_flags") or []):
                    # The PR is in a repo this hook cannot read the state of; only
                    # the run's own success can confirm it.
                    if not _run_succeeded_as_the_op(command, op, tool_response):
                        continue
                else:
                    from shared.gh_helpers import check_pr_state

                    if not _close_succeeded(command, op, tool_response) and (
                        check_pr_state(pr, cwd=cwd) != _CONFIRMING_STATE[op]
                    ):
                        continue
            elif isinstance(op, str) and op not in _CONFIRMING_STATE:
                pattern = LAYER1_SUCCESS_STDOUT_PATTERNS.get(op)
                # As in Layer 1, the run's own success stands in for a missing
                # success line; an eval of an assigned variable is read as the
                # eval of its value so its run can be the op's.
                if pattern is not None and not (
                    (isinstance(stdout_text, str) and pattern in stdout_text)
                    or _run_succeeded_as_the_op(_rewrite_variable_evals(command), op, tool_response)
                ):
                    continue
            try:
                os.rename(path, path + ".consumed")
            except OSError:
                pass  # a concurrent retire or cleanup won the race
            print(
                f"[security] merge-authorization token retired (exact command, op_type={op})",
                file=sys.stderr,
            )
            return True
    except Exception:  # noqa: BLE001 - any doubt keeps the token
        return False
    return False


_RUN_TIME_TARGET_MARKS = ("$", "`", "\x00")


def _target_set_at_run_time(command: str) -> bool:
    """Whether the command's target is fixed only when it runs: a variable, a
    substitution, or the implicit current branch (`gh pr merge "$PR"`, a variable
    branch to delete, a bare `gh pr merge`). Its token retires on the successful run
    itself, since no output line or PR state can say which target ran, and the same
    approval must not run a second, different target."""
    ctx = extract_command_context(
        _single_destructive_leg(command) or _single_detectable_leg(command) or command
    )
    target = _target_value(ctx)
    return isinstance(target, str) and any(mark in target for mark in _RUN_TIME_TARGET_MARKS)


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

    Match granularity (#1097 target-aware + #1100 flag-aware): a token is
    retired ONLY when it agrees with the executed command on op_type AND
    target AND bound_flags AND session. Target and flags are derived from ONE
    leg-isolated extract_command_context call, so retirement, mint, and read
    agree on identity BY CONSTRUCTION (the SAME _target_value + the SAME
    extract_privileged_flags SSOT the mint/read use). This finer match cures
    the over-retirement friction where a bare same-op/same-target command
    burned an operator's approved ESCALATED (flag-carrying) token, forcing a
    re-approval. Two independent, both-SAFE moves compose here, precisely:
    the flag-inequality skip is purely SUBTRACTIVE (it only ever declines to
    retire on a flag mismatch — fewer tokens on the flag axis); the
    leg-isolated cmd_target derivation PURIFIES the compare toward the
    executed op's OWN (op, target, flags) identity (mirroring the read arm,
    merge_guard_pre.py:536-540) and so can retire a self-consume a prior
    whole-command mis-scan would have MISSED — still the SAFE direction, since
    over-retire only CONSUMES a token, never authorizes one. Net: laundering-
    safe by construction — retirement only ever REMOVES tokens, never grants
    authorization; the sole residual is a tolerated under-retire (a genuine
    self-consume still retires; an unrelated command's leftover token is
    backstopped by TTL + MAX_USES), and over-block stays structurally
    IMPOSSIBLE (a PostToolUse observer renames after success, never gates).

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
    # #1097 + #1100: op+target+bound_flags retirement. Bring the match up to the
    # granularity the mint records — retire ONLY the token for the SPECIFIC operation
    # (same op AND same target AND same flag set), not any same-op token in the session.
    # Both target and the flag set are derived from ONE leg-isolated
    # extract_command_context call, mirroring the read arm (merge_guard_pre.py:536-540),
    # so retirement, mint, and read agree on (op, target, bound_flags) identity by
    # construction — the SAME _target_value + the SAME extract_privileged_flags SSOT.
    # Quoted single-destructive compounds MINT from their destructive leg
    # (#1195 OBS-H, the same three-tier surface via _extraction_surface), so this
    # leg selection is LOAD-BEARING for those tokens — retirement must select the
    # same leg the mint bound; the `or command` tail is the shared fail-safe
    # collapse when neither leg selector binds (ambiguity → whole command, the
    # unmintable / no-retire direction). (The API-merge form reuses pr_number per
    # #1096, so target coverage stays uniform with zero extra code.)
    cmd_ctx = extract_command_context(
        _single_destructive_leg(command)
        or _single_detectable_leg(command)
        or command
    )
    cmd_target = _target_value(cmd_ctx)
    cmd_flags = set(cmd_ctx.get("bound_flags", []))
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
        # The mint binds the op its leg reads as, which can differ from the whole
        # command's class (a forced push to main inside a trap's loop).
        if ctx.get("operation_type") not in (op_type, cmd_ctx.get("operation_type")):
            continue
        # #1097: target-aware ONLY when the token carries a target (every PRODUCTION-
        # minted token does — _collect_pairs mints no pair without a non-None
        # _target_value). A target-LESS token (a degenerate/legacy shape) has no target
        # to discriminate on, so it falls back to the pre-#1097 op+session match rather
        # than becoming un-retirable. SAFE: a target-less token is never the operator's
        # PROTECTED token (that one has a target), so the fallback cannot re-open the
        # over-block the fix cures — a real protected {op, target:42} token is NOT
        # retired by an unrelated {op, target:43} command (token_target=42 non-None,
        # 43 != 42 -> continue).
        token_target = _target_value(ctx)
        if token_target is not None and cmd_target != token_target:
            continue
        # #1100: bound_flags axis — retire ONLY when the executed command's binding-
        # relevant flag set EXACTLY equals the token's approved set. Byte-mirrors the
        # read arm's #1042 set-equality (merge_guard_pre.py:559). UNCONDITIONAL — unlike
        # the target skip above (which falls back to op+session for a target-LESS legacy
        # token), the empty set is a first-class flag universe here (a flagless base
        # command), so [] == [] correctly retires a flagless self-consume while a bare
        # `gh pr close 5` (cmd_flags == set()) no longer burns an approved ESCALATED
        # {close,5,[--delete-branch]} token. Conjunctive + MONOTONIC: this can only
        # retire FEWER tokens, so the sole residual is a tolerated under-retire
        # (backstopped by TTL + MAX_USES) — never an over-block (PostToolUse observer).
        # A token-has-flags guard would be WRONG: it would let a flagged command retire
        # a flagless token, breaking the symmetric identity match.
        token_flags = ctx.get("bound_flags", [])
        if not isinstance(token_flags, list) or not all(isinstance(x, str) for x in token_flags):
            # Robustness (Copilot #1110): a malformed/legacy token whose
            # bound_flags is not a list of strings would crash the whole scan
            # and skip retirement for every later token in the run. Two shapes:
            # (1) NON-LIST — e.g. JSON null -> Python None, since .get's []
            # default applies ONLY when the KEY is ABSENT, not when it is present
            # and null — makes set(token_flags) raise TypeError; (2) a list
            # CONTAINING an unhashable element (e.g. [{}] or [["x"]]) passes the
            # list check but still makes set(token_flags) raise (unhashable
            # type). The all(isinstance(x, str)) clause rejects BOTH: the mint
            # only ever writes a list of flag STRINGS (extract_privileged_flags
            # SSOT), so any non-str element is malformed. Skip it gracefully —
            # mirrors the isinstance(ctx, dict) guard above; a safe under-retire,
            # never a crash of the PostToolUse observer. A valid token's
            # bound_flags is always a list of strings (incl. []; all() over an
            # empty list is True), so valid-token semantics are unchanged.
            continue
        if set(token_flags) != cmd_flags:
            continue
        # Session scoping (SEC-S1 cycle-2 revised asymmetric predicate).
        if not _token_in_session(token_data, current_session):
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


# gh's result line for a PR (`✓ Squashed and merged pull request #5 (…)`,
# `✓ Closed pull request o/r#5 (…)`).
_GH_RESULT_RE = re.compile(r"(?im)\b(merged|closed) pull request (?:[\w.-]+/[\w.-]+)?#(\d+)\b")
# git push's line for a ref it updated (`+ a1...b2 feat/x -> feat/x (forced update)`,
# `   a1..b2  main -> main`, ` * [new branch] x -> x`); a rejected ref's line starts `!`.
_GIT_PUSH_UPDATE_RE = re.compile(r"(?m)^(?!\s*!).*?->\s*(\S+)")
_GIT_DELETED_RE = re.compile(r"(?m)^[ \t]*-[ \t]+\[deleted\]")
# A terminal colour code, which a coloured run's output carries.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# A failure line any git command, or another program, can print.
_GIT_FAILURE_LINE_RE = re.compile(r"^(?:fatal|error):")
# A push's own failure lines, which name no ref.
_GIT_PUSH_FAILURE_RE = re.compile(
    r"^(?:error: failed to push some refs|error: unable to delete '|error: src refspec .* does not match)"
)
# A bad remote's line, which any git command reaching that remote prints.
_GIT_NO_REMOTE_RE = re.compile(r"^fatal: '(.*)' does not appear to be a git repository")
# A network, access or upstream failure, which other git commands print too, with
# the remote's URL when it names one.
_GIT_REMOTE_ACCESS_RE = re.compile(
    r"^(?:fatal: unable to access '([^']*)'|fatal: repository '([^']*)' not found"
    r"|fatal: Authentication failed for '([^']*)'"
    r"|fatal: Could not read from remote repository|remote: Repository not found"
    r"|fatal: The current branch .* has no upstream)"
)
# gh's failure lines for a PR (`X Pull request #5 is not mergeable: …`, `GraphQL: …`,
# `HTTP 404: Not Found`, `no pull requests found for branch "x"`).
_GH_FAILURE_LINE_RE = re.compile(
    r"(?i)^\s*(?:[X\u2717\u00d7]\s|GraphQL:|HTTP \d{3}\b|(?:failed|could not|unable|error)\b)"
    r"|\bnot mergeable\b|\bno pull requests? found\b"
)


def _failure_output_shows_success(command: str, op_type: str, output: str) -> bool:
    """Whether a failed call's output shows its guarded op itself succeeded: gh's
    merged or closed line for the op's PR, git push's update line for the op's ref
    (any ref for a push that names none), git's deleted line for a remote ref, or
    `Deleted branch <b>` for the op's branch. Anything else keeps the token."""
    output = _ANSI_RE.sub("", output)
    ctx = extract_command_context(
        _single_destructive_leg(command) or _single_detectable_leg(command) or command
    )
    if op_type in ("merge", "close"):
        verb = "merged" if op_type == "merge" else "closed"
        pr = ctx.get("pr_number")
        return any(
            m.group(1).lower() == verb and (pr is None or m.group(2) == pr)
            for m in _GH_RESULT_RE.finditer(output)
        )
    if op_type in ("force-push", "push-to-main"):
        ref = ctx.get("target_ref")
        updated = {m.group(1).removeprefix("refs/heads/") for m in _GIT_PUSH_UPDATE_RE.finditer(output)}
        return bool(updated) if not isinstance(ref, str) else ref.removeprefix("refs/heads/") in updated
    if op_type == "remote-ref-delete":
        return _git_reports_ref_deleted(command, op_type, {"stderr": output})
    if op_type == "remote-mass-delete":
        return _GIT_DELETED_RE.search(output) is not None
    if op_type == "branch-delete":
        branch = ctx.get("branch")
        return isinstance(branch, str) and re.search(
            r"(?m)^Deleted branch " + re.escape(branch) + r" \(", output
        ) is not None
    return False


def _names(line: str, name: str) -> bool:
    """Whether `line` names the ref or branch `name` as a word of its own."""
    return re.search(r"(?<![\w/.-])(?:refs/heads/)?" + re.escape(name) + r"(?![\w/.-])", line) is not None


def _op_is_only_leg(command: str) -> bool:
    """Whether the command is a single leg, and the string a shell's -c runs in it
    is a single leg too, so any line in its output is the op's own. Any other leg,
    before or after, may print git's lines through a wrapper, subshell or script."""
    if len(_split_into_legs(command)) != 1:
        return False
    inner = shell_c_string(command)
    return inner is None or len(_split_into_legs(inner)) == 1


def _failure_output_shows_op_failed(command: str, op_type: str, output: str) -> bool:
    """Whether a failed call's output shows its guarded op's own failure: for a
    push, git's refused line (`!`) for the op's ref (any ref for a push that names
    none), a failure line only a push prints that names the op's ref, or a
    bad-remote, network, access or upstream line quoting the remote the push names;
    for a branch delete, an `error:` line naming the branch; for a merge or close,
    gh's failure line. Any other `fatal:` or `error:` line, and an unquoted remote
    line, counts only when the op is the command's only leg, since another leg
    could have printed it.
    Colour codes are read past; splitlines() also ends a line at a carriage return."""
    lines = _ANSI_RE.sub("", output).splitlines()
    if op_type in ("merge", "close"):
        return any(_GH_FAILURE_LINE_RE.search(line) for line in lines)
    ctx = extract_command_context(
        _single_destructive_leg(command) or _single_detectable_leg(command) or command
    )
    only_leg = _op_is_only_leg(command)
    if op_type == "branch-delete":
        branch = ctx.get("branch")
        return any(
            (_GIT_FAILURE_LINE_RE.match(line) and only_leg)
            or (line.startswith("error:") and isinstance(branch, str) and _names(line, branch))
            for line in lines
        )
    ref = ctx.get("target_ref")
    own = [str(f)[len("remote="):].rstrip("/") for f in ctx.get("bound_flags") or [] if str(f).startswith("remote=")]

    def quotes_own_remote(line: str) -> bool:
        m = _GIT_NO_REMOTE_RE.match(line) or _GIT_REMOTE_ACCESS_RE.match(line)
        quoted = next((g for g in m.groups() if g), "") if m else ""
        return bool(quoted) and quoted.rstrip("/") in own

    return any(
        (only_leg and (_GIT_FAILURE_LINE_RE.match(line) or _GIT_REMOTE_ACCESS_RE.match(line)))
        or (_GIT_PUSH_FAILURE_RE.match(line) and isinstance(ref, str) and _names(line, ref))
        or quotes_own_remote(line)
        or (line.lstrip().startswith("!") and (not isinstance(ref, str) or _names(line, ref)))
        for line in lines
    )


def _failed_call_keeps_token(command: str, op_type: str, output: str, frame_cwd: object) -> bool:
    """Whether a failed call's token stays for a retry: never when the op's own
    success line shows; else, for a merge or close whose PR state can be read,
    when that state is not the one the op produces, whatever the output says;
    else when the op's own failure shows. Any other doubt retires it."""
    if _failure_output_shows_success(command, op_type, output):
        return False
    if op_type in _CONFIRMING_STATE:
        state = _pr_state(_merge_reading(command) if op_type == "merge" else command, frame_cwd)
        if state:
            return state != _CONFIRMING_STATE[op_type]
    return _failure_output_shows_op_failed(command, op_type, output)


def _retire_after_failed_call(input_data: dict) -> None:
    """A Bash call whose guarded op ran and whose later step failed ends in
    PostToolUseFailure. Its `error` text carries the call's stdout and stderr, cut to
    its head and tail when long. A quiet, porcelain, coloured or truncated success
    prints no success line, so the token retires unless the output or the PR state
    shows the op failed (_failed_call_keeps_token): one approval, one op. A failed op
    whose failure does not show, or an op an earlier `&&` leg kept from running,
    costs one re-approval. An interrupted call or a dry run keeps the token. Never
    raises."""
    try:
        tool_input = input_data.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        output = input_data.get("error")
        if (
            input_data.get("tool_name") != "Bash"
            or not isinstance(command, str)
            or not isinstance(output, str)
            or input_data.get("is_interrupt") is True
        ):
            return
        # Read as the mint reads it: an eval of an assigned variable runs its value, and
        # a trap's handler, a printed string or a set of positionals run what they hold.
        runs = _rewrite_readable_evals(command)
        op_type = detect_command_operation_type(runs)
        if (
            op_type
            and not _op_did_not_run(runs)
            and not _failed_call_keeps_token(runs, op_type, output, input_data.get("cwd"))
        ):
            # A token bound to the command's text (a string run by `bash -c`, eval or a
            # trap) retires on the same decision, as on the success path.
            _retire_exact_text_token(command, input_data.get("cwd"))
            _retire_token_for_command(runs, op_type)
    except Exception:  # noqa: BLE001 - any doubt keeps the token
        return


def main():
    """Main entry point for the PostToolUse hook."""
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        pact_context.init(input_data)
        # A failed call is not a successful run: only its output can retire a token.
        if input_data.get("hook_event_name") == "PostToolUseFailure":
            _retire_after_failed_call(input_data)
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        tool_input = input_data.get("tool_input", {})
        # The command's git aliases are read from the directory it runs in.
        set_command_directory(input_data.get("cwd"))
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
            # A git alias runs the command its value names, which is how the
            # classifier reads it, so its run retires as that command; an
            # exact-command token still matches the text that was run.
            runs = _rewrite_git_aliases(command) if isinstance(command, str) else command
            if (
                isinstance(command, str)
                and tool_response.get("interrupted") is not True
                and not _op_did_not_run(runs)
            ):
                _retire_exact_text_token(
                    command, input_data.get("cwd"), stdout_text=tool_response.get("stdout", ""),
                    tool_response=tool_response,
                )
            command = runs

            # Block 1 — Command-shape filter (SEC-S2: extends from merge-
            # only to all op_types in LAYER1_SUCCESS_STDOUT_PATTERNS).
            # An op_type of None (classifier didn't recognize the command)
            # OR an op_type not in the table is rejected. The `in` check
            # against the dict keys is the SSOT for which op_types Layer 1
            # observes.
            op_type = detect_command_operation_type(command)
            # An op with no row in the table (a remote ref delete) still retires
            # when the run's own success shows the op succeeded.
            if op_type not in LAYER1_SUCCESS_STDOUT_PATTERNS and not (
                op_type
                and (
                    _run_succeeded_as_the_op(command, op_type, tool_response)
                    or _git_reports_ref_deleted(command, op_type, tool_response)
                )
            ):
                print(_SUPPRESS_OUTPUT)
                sys.exit(0)

            # Block 2 — Platform-level success signal (non-interrupted).
            # PostToolUse only fires on tool-call success at the platform
            # layer (failed Bash routes to PostToolUseFailure per Agent
            # SDK). extract_tool_response always returns a dict: a
            # non-dict or absent response arrives here as {}, carries no
            # stdout, and so retires only if Block 3's state check
            # confirms the PR's final state.
            if tool_response.get("interrupted") is True or _op_did_not_run(command):
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
            expected_substring = LAYER1_SUCCESS_STDOUT_PATTERNS.get(op_type)
            # A merge whose PR an earlier leg assigns a literal number is judged as
            # that number, so the merge rule below applies to it.
            judged = _merge_reading(command) if op_type == "merge" else command
            if expected_substring is not None and not _target_set_at_run_time(judged):
                stdout_text = tool_response.get("stdout", "")
                if not isinstance(stdout_text, str):
                    print(_SUPPRESS_OUTPUT)
                    sys.exit(0)
                if op_type in _CONFIRMING_STATE:
                    # A piped or non-TTY gh run prints no "Merged pull request"
                    # line, so a missing substring falls back to asking GitHub
                    # for the PR's state; any doubt there keeps the token.
                    # A readable state outranks the exit status: a merge that
                    # exits 0 into a merge queue or with --auto leaves the PR
                    # OPEN and keeps its token. Only an unreadable state falls
                    # back to the run's own success. A close has no deferred
                    # form, so its successful run retires without the state.
                    confirmed = expected_substring.lower() in stdout_text.lower() or _close_succeeded(
                        command, op_type, tool_response
                    )
                    if not confirmed:
                        state = _pr_state(judged, input_data.get("cwd"))
                        confirmed = state == _CONFIRMING_STATE[op_type] or (
                            not state and _run_succeeded_as_the_op(_resolved_evals(command), op_type, tool_response)
                        )
                else:
                    confirmed = expected_substring in stdout_text or _run_succeeded_as_the_op(
                        command, op_type, tool_response
                    )
                if not confirmed:
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
        # (extract_tool_response always returns a dict; the answers value may not be one.)
        answers = tool_response.get("answers", {})
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
                # Observer-style confirmation (exit 0): name what the approval
                # authorizes, so a click that was not an approval of this command
                # is caught before the command runs. The token is unchanged.
                print(json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": _mint_confirmation(context),
                    }
                }))
                sys.exit(0)
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
