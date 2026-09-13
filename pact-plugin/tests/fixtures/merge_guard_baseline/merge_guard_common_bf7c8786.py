"""
Location: pact-plugin/hooks/shared/merge_guard_common.py
Summary: Shared constants and utilities for the merge guard hook pair.
Used by: merge_guard_pre.py (PreToolUse) and merge_guard_post.py (PostToolUse)

================================ THREAT MODEL ================================
HONEST-MISTAKE PREVENTION — read this BEFORE "hardening" the guard.

The merge guard exists to route an HONEST destructive command through the
operator's AskUserQuestion approval click: it catches an agent about to run a
real destructive git/gh operation BY MISTAKE and asks the operator to confirm.
It is NOT an adversarial sandbox and makes NO attempt to stop a determined
evader.

INVARIANT (supersedes everything): a faithful single-command click ALWAYS mints
a token and executes. A faithful operator clicks the option carrying the command
(`gh pr merge 5`, `gh pr close 5 --delete-branch`, even `gh pr merge 5 --admin`)
→ it mints → the command runs. ANYTHING that can block a faithful single-command
click is WRONG BY DEFINITION — over-blocking a faithful click is a worse failure
than not catching an obfuscated one.

EXPLICITLY OUT OF SCOPE (these are NOT bugs; do NOT "fix" them — a blind
adversarial sweep that "finds" them must NOT re-trigger hardening, because the
hardening that catches them also blocks faithful clicks):
  - quote-concat / quote-elision in the op keyword: `gh pr ''merge`,
    `gh pr "merge"`, `g'h' pr merge`, `gh pr m'erge'` — an honest agent does not
    obfuscate the command it intends to run.
  - command-as-data via an interpreter pipe / substitution / eval:
    `echo '...' | sh`, `$(echo '...')`, `eval "$CMD"` — deliberate evasion, not a
    mistake.
  - runtime $-expansion hiding the op or a flag: `gh pr $VERB 5`,
    `gh pr merge 5 $FLAGS` — the hook only sees the pre-expansion literal an
    honest agent typed.
  - attached / equals API flag-spelling evading the literal pattern:
    `gh api -XDELETE`, `--method=DELETE`.
A metachar/quote SUPPRESSOR for the above (the removed shell-semantic over-block
layer) re-blocks faithful clicks — e.g. it over-blocked
`gh pr close 7 --comment "(done)" --delete-branch` — which is why it was removed.
Keep detection LITERAL and faithful-click-safe; do not re-introduce an
adversarial parser or a fail-closed metachar/quote SUPPRESSOR. This is distinct from
the KEPT additive flag-normalization arm (_flag_condition_danger_op), which only ADDS
recognition of canonical flag spellings via a quote-aware tokenize and ABSTAINS on a
parse failure — it can only OVER-block, never suppress, so do NOT strip it as
"non-literal."

What the guard DOES recognize (the honest-command surface only): the literal
destructive patterns (DANGEROUS_PATTERNS); canonical flag SPELLINGS an honest
agent actually types (close `-d`/`-cd`, branch `-Df`/`-fD`); the privileged-flag
bind (set-equality of approved vs executed flags, so an honest re-run that ADDS a
privilege re-prompts rather than silently escalating); and faithful-click region/
quote handling so a quoted argument never truncates the approved command.

rm-EXCEPTION (deliberate, documented so a future sweep does NOT "discover" a gap):
the compound-destructive count (is_compound_destructive_command) treats a plain-`rm`
head leg as destructive, so a recognized git/gh op chained with an `rm`
(`gh pr merge 5 && rm -rf /`) is refused as a 2-destructive-leg mistake. This is
rm-SPECIFIC by design — NOT a general filesystem-destroyer detector: `dd`, `mkfs`,
`shred`, `truncate`, etc. are OUT OF SCOPE (do NOT add them — honest-mistake posture,
no obfuscation-chasing: plain `rm` head only, not `/bin/rm`, `r''m`, `$(echo rm)`).
And rm is deliberately ABSENT from is_dangerous_command, so a BARE `rm -rf /` and a
PURE-rm chain (`rm -rf a && rm -rf b`) stay is_dangerous=False and are NEVER gated —
the guard stays out of pure-filesystem commands.

benign-CONTINUATION (the dual of the rm-EXCEPTION; documented so a future sweep does
NOT "discover" the single-leg mint and "harden" it away): for EVERY recognized
destructive op — gh pr merge, gh pr close, git force-push, AND git branch-delete — a
SINGLE such op + ANY benign continuation mints a token AND the read side authorizes the
continued command. "Benign" means the remainder is NOT a second destructive git/gh op —
it need NOT be read-only (a `tee` or an output redirect WRITES a file and is still
benign). A benign chain (`gh pr merge 5 && echo ok`, `gh pr merge 5 ; echo done`), a
backgrounding (`gh pr merge 5 &`), a pipe to a pager/filter or `tee`
(`gh pr merge 5 | tail`, `gh pr merge 5 | tee log`), or an output / fd redirect
(`gh pr merge 5 > out.log`, `git push --force origin main 2>&1`,
`git branch -D feature > log`) is a faithful single-command click:
is_compound_destructive_command refuses ONLY on >=2 destructive legs (one destructive
leg + benign continuation is NOT compound), and the read-side target is re-derived from
the SINGLE destructive leg — the merge/close PR-NUMBER positional, the branch-delete
name, OR the force-push ref — regardless of any trailing continuation / redirect tokens,
so the approval still binds. (The positional extractors truncate at the first benign
terminator on the quote-masked view; the read call site isolates the single destructive
leg before deriving op/target/flags.) This is the GENERAL single-destructive-op-plus-
benign-remainder pattern, NOT a recognition allow-list of viewers/filters — do NOT
enumerate viewers in detection logic (an allow-list drifts and would re-block faithful
clicks the count already mints).

conservative-RECOGNITION (the design rule behind the accepted compound under-block;
documented so a future sweep does NOT "discover" these forms and "harden" them into
faithful-click over-blocks): recognition targets the SINGLE destructive command an honest
agent runs (the destructive op plus the benign viewers/filters/redirects of benign-
CONTINUATION above) and ERRS TOWARD LETTING THROUGH — over-blocking a faithful click is
WRONG BY DEFINITION (the INVARIANT above), worse than missing a buried op. The ENTIRE
flag-condition union arm (_flag_condition_danger_op) needs a positional, quote-aware
parse — the close/branch-delete/force-push flag conditions AND the git-push
remote-ref-delete (`:ref` / `--delete` / `-d`) / mass-delete (`--mirror` / `--prune` /
multi-ref) extractors — so its recognition is ANCHORED to the FIRST executable leg (the
_executable_prefix view): it does NOT chase those ops into NON-FIRST compound legs, and
it derives FLAGS from that same leg (deriving flags from the WHOLE command while
positionals came from the first leg let a force/delete flag in a benign continuation leg
mislabel a benign first-leg op — the cross-leg flag leak). Chasing them needs a
match-anywhere / per-leg scan that fires on a quoted `:ref` / `--mirror` mention in a
benign leg — an over-block of a faithful click. The ACCEPTED price is that these forms
run UNGATED when the destructive op is not the first leg:
  - `cd /repo && git push origin --delete main`
  - `git fetch && git push --mirror origin`
  - `NOTE=x ; git push origin :main`
  - `cd /repo && git branch -Df temp`   (cluster force-delete; idiomatic `-D` still caught per-leg, in any leg)
  - `cd /repo && gh pr close 5 -d`      (short `-d` close; spelled `--delete-branch` still caught per-leg, in any leg)
These are NOT bugs — do NOT "fix" them (the fix re-blocks faithful clicks). httpie
(`http` / `https` CLI) is likewise WHOLLY ungated by design — ref-mutation, merge, AND
protection-mutation — because the MINT classifier covers gh-api / curl / wget only; ANY
httpie read-floor arm re-creates a gated-but-unmintable over-block. Ungated keeps read == mint.
NB this first-leg anchoring is SPECIFIC to those parse-dependent forms: the LITERAL
danger arms (the DANGEROUS_PATTERNS bank + the per-leg literal-arm tuples: force-push,
branch-delete, close, API ref/protection) match in ANY leg position (per-leg for the
tuple arms) and STILL gate in a non-first leg
(`cd /repo && git push --force origin main` is caught). When an over-block of a faithful
click is found, the fix WIDENS the mint, never narrows detection into a new under-block.
=============================================================================

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
security check; the primary check is I-3 TTL expiry (bounded by TOKEN_TTL).
"""

from __future__ import annotations

import glob
import os
import re
import shlex
import time
from collections.abc import Sequence
from pathlib import Path

from .paths import get_claude_config_dir

# Token TTL in seconds
TOKEN_TTL = 900

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

# Orphan-token cleanup threshold, derived as a fixed multiple of TOKEN_TTL so the
# two never drift. Tokens that survive past this window without being consumed or
# used are reaped as disk hygiene — they cannot be legitimate (TOKEN_TTL already
# expires them for authorization). 12x TOKEN_TTL gives strong margin against any
# legitimate in-flight token while aggressively bounding accumulation. Disk-hygiene
# defense — not a primary security check; the primary check is TOKEN_TTL expiry
# (invariant I-3).
ORPHAN_TOKEN_MAX_AGE_SECONDS = 12 * TOKEN_TTL

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
    # push-to-main (GAP3): a plain `git push` to main/master — emits to STDERR like
    # force-push, so None (Block 3 skipped; Block 2 platform-success is load-bearing,
    # fail-closed-on-no-signal → TTL/MAX_USES safety net, not bypass).
    "push-to-main": None,
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

# The quote-BALANCED value token (#1118 re-model) — the single Q-SAFE primitive the
# verb-message value strips consume via _strip_flag_values (arm 3, shared by carriers
# 5/7/7b/7c/7d/8/9). A value token is a maximal run of bash quoted/escaped word pieces,
# ending at unquoted whitespace. Because it consumes quoted spans ATOMICALLY it can never
# bite a PARTIAL quoted span (a lone quote is not a complete `'…'`/`"…"`/`$'…'`), so it
# structurally CANNOT emit a dangling quote into the `stripped` string that
# _mask_shell_quotes / _slice_stripped_legs consume — the root-cause fix for the SEC-1/SEC-2
# leg-merge regression. Its arm set mirrors _VERB_MSG_BODY below (bash's five quoting
# mechanisms + backslash-escape), differing only in the plain-char class `[^\s'"$\\]` (a
# value ends at unquoted whitespace) and the closing `+`. Inner quoted-span alternations are
# first-char-disjoint (linear); the outer arms overlap only at the three `$`-initial arms, a
# bounded per-unit ambiguity (both partitions reach the same offset) that cannot compound,
# and the token matches only in trailing position so the greedy match never backtracks.
# Linearity is verified empirically (the regex-perf suite + adversarial `$'…'`-repetition
# timing).
_VALUE_TOKEN = (
    r"""(?:\\.|\$'(?:[^'\\]|\\.)*'|\$?"(?:[^"\\]|\\.)*"|'[^']*'|[^\s'"$\\]|\$)+"""
)

# Bash-faithful verb-message span BODY: a command's words from the verb to the first
# UNQUOTED ;/&&/|/newline. Models ALL FIVE bash quoting mechanisms + backslash-escape so
# it can NEVER desync quote-pairing and pair a `'` across a separator (the naive
# `'[^']*'`-only body's leg-merge under-block). Arms in order:
#   \\.                        backslash-escape OUTSIDE quotes (\'  \;  \&  \"  \\ …)
#   \$'(?:[^'\\]|\\.)*'        ANSI-C  $'...'  (backslash honored inside)
#   \$?"(?:[^"\\]|\\.)*"       double-quote "..." AND locale $"..." (backslash honored)
#   '[^']*'                    single-quote '...'  (bash: NO escaping inside)
#   [^&|;\n"'$\\]+             plain chars (excl. separators, quotes, $, backslash)
#   \$                         a bare $ (e.g. $VAR)
# Separators ;&|newline are excluded from EVERY arm, so the span stops at a real unquoted
# separator. Shared SSOT for all verb-message carriers (5, 7/7b/7c/7d, curl) — replaces 7
# former inline copies so they can never drift.
_VERB_MSG_BODY = (
    r"""(?:\\.|\$'(?:[^'\\]|\\.)*'|\$?"(?:[^"\\]|\\.)*"|'[^']*'|[^&|;\n"'$\\]+|\$)*"""
)

# Shared message-flag anchor (flag_sep group 1) for the sibling message-carrying git
# verbs whose SOLE value-taking --m* option is --message — git merge, git stash
# push/store, git notes add/append (verified via `git <verb> -h`, 2.50.1). The long
# arm accepts any unambiguous abbreviation of --message (--m -> --message); the short
# arm covers -m / bundled / attached. EXACTLY ONE capturing group (internals are all
# non-capturing) so it satisfies the _strip_flag_values contract. This is byte-identical
# to the git-commit anchor (carrier-5) but kept as a SEPARATE constant: the commit
# carrier stays a self-contained literal, and git tag needs a DIFFERENT bounded variant
# (its --merged/--no-merged collision), so the three are intentionally not unified.
_MSG_FLAG_ANCHOR = r"((?:--m(?:e(?:s(?:s(?:a(?:g(?:e)?)?)?)?)?)?|-[a-ln-zA-Z]*m)\s*)"

# Pre-compiled patterns for the operation-type classifier (consistent with
# DANGEROUS_PATTERNS style).
_GH_PR_MERGE_RE = re.compile(_GH_PREFIX + r"pr\s+merge\b")
_GH_PR_CLOSE_RE = re.compile(_GH_PREFIX + r"pr\s+close\b")

# Literal force-push arms — ONE arm-list SSOT consumed by BOTH the read floor
# (is_dangerous_command) and the mint classifier (detect_command_operation_type),
# so the two sides can never drift on a spelling. Matched PER-LEG over the shared
# leg-boundary substrate (#1082): these arms' `.*` spans previously ran over the
# WHOLE command, so a force-class flag in a benign continuation leg
# (`git push origin feature && rm -f stale.txt`) gated the benign first-leg push —
# one form (`git push && rm -f x.txt`) PERMANENTLY (no extractable target ->
# unmintable). Semantics now: an arm fires iff push and the force-class flag
# co-occur within ONE leg, in ANY leg position — `cd /repo && git push --force
# origin main` still gates (deliberately DIFFERENT from the union arm's first-leg
# anchoring; the literal floor keeps its match-anywhere purpose, per leg). Quoted
# separators are handled by the substrate (a quoted `&&` is not a leg boundary) —
# a tempered-regex span (`[^&|;]*`) would wrongly ungate that form.
_FORCE_PUSH_LITERAL_ARMS = (
    re.compile(_GIT_PREFIX + r"push\s+.*--force(?!-with-lease)\b"),
    re.compile(_GIT_PREFIX + r"push\s+.*-f\b"),
    re.compile(_GIT_PREFIX + r"push\s+-[a-zA-Z]*f"),
)

# leg-boundary substrate (#1087): these close arms' `.*`/lookahead previously ran
# over the WHOLE stripped command, so a `--delete-branch` token in a benign
# continuation leg fired the arm cross-leg. That over-reach ALSO laundered: the
# ambiguous multi-close `gh pr close 42 && gh pr close 43 && echo --delete-branch`
# was is_dangerous=True whole-command, so it minted a close token that AUTHORIZED an
# escalated same-target single `gh pr close 42 --delete-branch`. Semantics now: an
# arm fires iff `gh pr close` and `--delete-branch` co-occur within ONE leg, in ANY
# leg position — so the ambiguous compound is is_dangerous=False per-leg (the mint
# write-gate refuses -> no token -> laundering structurally dead) while a real
# single-leg `gh pr close 42 --delete-branch` still gates. Quoted separators are
# handled by the substrate (a quoted `&&` is not a leg boundary). Bodies are moved
# VERBATIM from DANGEROUS_PATTERNS — the conversion changes WHERE they match
# (per-leg vs whole-command), never WHAT they match.
_CLOSE_LITERAL_ARMS = (
    re.compile(_GH_PREFIX + r"pr\s+close\b(?=.*--delete-branch)"),   # forward
    re.compile(r"--delete-branch.*" + _GH_PREFIX + r"pr\s+close\b"),  # reversed
)

# leg-boundary substrate (#1086): these 17 API danger arms' `.*` previously ran
# over the WHOLE stripped command, so a mutating method / body-flag token in a
# benign continuation leg (`gh api .../git/refs && echo -X DELETE`) over-blocked
# the benign compound. Matched PER-LEG now: an arm fires iff the API client, the
# mutating method (or implicit-POST body flag), and the target endpoint co-occur
# within ONE leg. The negative-lookahead arms operate WITHIN a leg — a same-leg
# explicit GET correctly excludes, and a body flag in a DIFFERENT leg no longer
# wrongly includes. Bodies are moved VERBATIM from DANGEROUS_PATTERNS (flags —
# re.IGNORECASE — lookaheads, and _GH_API_PREFIX/curl/wget prefixes unchanged);
# the conversion changes WHERE they match, never WHAT they match. No-laundering
# safety is preserved BY CONSTRUCTION, not by denylist-emptiness: an isolated API
# leg is method-less hence detect-negative, so tier 2 abstains → symmetric
# whole-command bind (the safe direction) — see TestEmergentDangerClassIsCloseOnly.
_API_LITERAL_ARMS = (
    # API-based merge bypasses (require mutating HTTP method to avoid blocking reads)
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X|--method)\s+(?:PUT|PATCH|POST)\b).*merge", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X|--request)\s+(?:PUT|PATCH|POST)\b).*api.*merge", re.IGNORECASE),
    # API-based branch deletion via DELETE to git/refs endpoint.
    # HOST-AGNOSTIC (#1061): the curl arms drop the literal `.*api.*` substring so a
    # truly api-free Enterprise/proxy URL (e.g. https://git.example.com/repos/o/r/git/refs/...)
    # no longer bypasses — bringing curl to parity with the already-host-agnostic
    # gh-api/wget arms. The `api` key was an as-shipped heuristic (#268/#271), not a
    # deliberated scope ruling; this WIDENS it. The over-block this widening would introduce
    # on a quoted `-d` body mentioning git/refs is closed by carrier-8 (the HTTP-client
    # data-body strip in _strip_non_executable_content) — the body value is stripped while
    # the path-resident ref survives (PATH-vs-BODY invariant).
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X|--method)\s+DELETE\b).*git/refs", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X|--request)\s+DELETE\b).*git/refs", re.IGNORECASE),
    # API-based ref mutation / force push via mutating method to git/refs endpoint
    # (any mutating operation on git refs via API is inherently dangerous). Curl arm is
    # host-agnostic (#1061) — see the DELETE arm note above.
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X|--method)\s+(?:PATCH|POST|PUT)\b).*git/refs", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X|--request)\s+(?:PATCH|POST|PUT)\b).*git/refs", re.IGNORECASE),
    # Branch-protection API mutation (#1063): DELETE|PUT|PATCH on a
    # `branches/<branch>/protection` endpoint WEAKENS protection (remove / replace /
    # modify whole config). POST is EXCLUDED — it ENABLES protection sub-features
    # (enforce_admins / required_signatures) = the STRENGTHENING direction, so gating it
    # would over-block. HOST-AGNOSTIC (the #1061 lesson — no `.*api.*`). Explicit-method
    # arms only (the protection endpoint has no implicit-POST danger like git/refs). The
    # branch is PATH-resident, so carrier-8 never strips it (no preservation guard needed,
    # unlike the body-resident contents arm). No httpie arm: the mint classifier's
    # `_is_api_form` is gh-api/curl/wget only, so adding an httpie read arm would create a
    # gated-but-unmintable over-block — omitted by design (httpie is WHOLLY out of charter;
    # its ref-mutation/merge arms were removed with it).
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X|--method)\s+(?:DELETE|PUT|PATCH)\b).*branches/.*/protection", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X|--request)\s+(?:DELETE|PUT|PATCH)\b).*branches/.*/protection", re.IGNORECASE),
    re.compile(r"\bwget\b(?=.*--method=(?:DELETE|PUT|PATCH)\b).*branches/.*/protection", re.IGNORECASE),
    # gh api implicit POST: body param flags (-f, -F, --field, --raw-field, --input)
    # cause gh api to default to POST. Dangerous when targeting git/refs or merge.
    # Negative lookahead excludes explicit GET (which overrides implicit POST).
    re.compile(_GH_API_PREFIX + r"(?!.*(?:-X|--method)\s+GET\b)(?=.*(?:-f|-F|--field|--raw-field|--input)\s).*git/refs", re.IGNORECASE),
    re.compile(_GH_API_PREFIX + r"(?!.*(?:-X|--method)\s+GET\b)(?=.*(?:-f|-F|--field|--raw-field|--input)\s).*merge", re.IGNORECASE),
    # curl implicit POST: --data/-d/--data-raw/--data-binary flags cause curl to
    # default to POST. Dangerous when targeting git/refs or merge API endpoints.
    # Negative lookahead excludes explicit GET (which overrides implicit POST).
    re.compile(r"\bcurl\b(?!.*(?:-X|--request)\s+GET\b)(?=.*(?:--data(?:-(?:raw|binary))?|-d)\s).*git/refs", re.IGNORECASE),
    re.compile(r"\bcurl\b(?!.*(?:-X|--request)\s+GET\b)(?=.*(?:--data(?:-(?:raw|binary))?|-d)\s).*api.*merge", re.IGNORECASE),
    # Contents API: write operations (PUT/PATCH/POST) to /contents/ endpoint
    # targeting main or master branch. Flags any mutating /contents/ call that
    # mentions main or master anywhere in the command (acceptable false positive).
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X|--method)\s+(?:PUT|PATCH|POST)\b).*contents/.*(?:main|master)", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X|--request)\s+(?:PUT|PATCH|POST)\b).*api.*contents/.*(?:main|master)", re.IGNORECASE),
    # Alternative HTTP clients: wget with --method flag
    re.compile(r"\bwget\b(?=.*--method=(?:DELETE|PATCH|POST|PUT)\b).*git/refs", re.IGNORECASE),
    re.compile(r"\bwget\b(?=.*--method=(?:DELETE|PATCH|POST|PUT)\b).*merge", re.IGNORECASE),
    # Known API detection gaps (defense-in-depth, not a security boundary):
    # - GraphQL mutations: gh api graphql -f query='mutation { ... }' bypasses REST-path matching
    # - gh alias: aliases can hide API calls (tracked in #270)
)

# leg-boundary substrate (#1094): these three branch-delete arms' `.*` previously
# ran over the WHOLE stripped command, so an idiomatic `-D` / `--delete --force`
# token in a benign continuation leg (`git branch new-feature && echo -D`) gated
# the benign compound — and, because the whole-command classifier ALSO returned
# branch-delete for it, the ambiguous compound was MINTABLE from a click whose
# branch leg was benign (the close-twin laundering shape). Matched PER-LEG now:
# an arm fires iff `git branch` and the force-delete flag co-occur within ONE
# leg, in ANY leg position (`cd /repo && git branch -D temp` still gates —
# match-anywhere purpose preserved, per leg). Quoted separators are handled by
# the substrate (a quoted `&&` is not a leg boundary). Bodies are moved VERBATIM
# from DANGEROUS_PATTERNS — the conversion changes WHERE they match (per-leg vs
# whole-command), never WHAT they match. Per-leg scoping also bounds the arms'
# `\s+` runs: whole-command they DID span a raw newline — the one whitespace-class
# leg separator — so `git branch --force<newline>--delete x` (two non-destructive
# legs) previously gated; bounding `\s+` is the same over-block-cure direction. Clustered /
# split spellings (`-Df` / `-fD` / `--delete -f`) are NOT these arms' job: they
# are the union arm's (first-leg-anchored, by design).
_BRANCH_DELETE_LITERAL_ARMS = (
    re.compile(_GIT_PREFIX + r"branch\s+.*-D\b"),
    re.compile(_GIT_PREFIX + r"branch\s+.*--delete\s+--force\b"),
    re.compile(_GIT_PREFIX + r"branch\s+--force\s+--delete\b"),
)


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
        "force-push"    - git push --force / git push -f (excludes --force-with-lease);
                          API PATCH/POST/PUT to git/refs (ref rewrite)
        "push-to-main"  - git push <remote> main/master WITHOUT --force, incl.
                          --force-with-lease pushes (review-bypass, distinct from
                          force-push so neither token authorizes a force-push; plain
                          and lease pushes mint DIFFERENT tokens via the
                          --force-with-lease presence bind in PRIVILEGED_FLAGS)
        "branch-delete" - git branch -D / git branch --delete --force / gh pr close --delete-branch;
                          API DELETE to git/refs (ref removal)
        "remote-ref-delete" - git push delete of a SINGLE remote ref (#1062a):
                          push <remote> :ref / --delete ref / -d ref (incl. implicit
                          remote). Union-arm-only; recognized IFF a single deletable
                          ref is extractable (recognition⟺mintability by construction)
        "remote-mass-delete" - git push MASS delete (#1062b): --mirror / --prune /
                          multi-ref delete. Union-arm-only; recognized IFF a normalized
                          mass-target tuple is extractable (single-ref defers to
                          remote-ref-delete — the BOUNDARY discriminator, no double-classify)
        "branch-protection" - API mutation WEAKENING branch protection (#1063):
                          gh-api/curl/wget DELETE|PUT|PATCH on a branches/<b>/protection
                          endpoint (host-agnostic; POST EXCLUDED = strengthening direction)
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
    # Legs are computed ONCE here and shared by the three per-leg loops below
    # (force-push, api-merge, branch-delete) — the same single-substrate hoist
    # idiom as the read floor's `legs` in is_dangerous_command.
    legs = _split_into_legs(command)
    # force-push: git push ... --force (excludes --force-with-lease — carved out
    # of the force-push arms ONLY; the push-to-main arm still gates lease pushes
    # to a default branch). Matched PER-LEG over the shared _FORCE_PUSH_LITERAL_ARMS
    # SSOT (#1082) — the same arm list the read floor consumes, over the same leg
    # substrate (_split_into_legs), so a force-class flag in a benign continuation
    # leg no longer classifies the first-leg push as force-push and read==mint
    # holds by construction on this class.
    for _leg in legs:
        if any(arm.search(_leg) for arm in _FORCE_PUSH_LITERAL_ARMS):
            return "force-push"
    # Direct push to a default branch (main/master) — plain OR --force-with-lease —
    # is a review-bypass, a DISTINCT op from force-push. Returning its own
    # `push-to-main` op (rather than folding into force-push) closes the
    # token-collapse where a plain-push approval authorized a force-push. WITHIN the
    # class, plain and lease pushes mint DIFFERENT tokens via the --force-with-lease
    # presence bind (PRIVILEGED_FLAGS; the close/--delete-branch precedent), so a
    # plain-push token can never authorize a lease push (which CAN rewrite history).
    # The --force/-f checks ABOVE run FIRST, so a forced push to main returns
    # force-push and never reaches here; ordering is load-bearing. The flag-walk is
    # byte-identical to the read floor's push-to-main arm (mint==read parity at the
    # source; the old lease-excluding lookahead here was a gated-but-unmintable
    # over-block: the read floor gated the lease push while the mint refused it, so
    # a faithful click was permanently blocked). The READ floor gates BOTH forms
    # (DANGEROUS_PATTERNS unchanged). Uses the same `(?!:)` refspec exclusion as
    # DANGEROUS_PATTERNS push-to-main.
    if re.search(_GIT_PREFIX + r"push\s+\S+\s+HEAD:(?:main|master)\b", command):
        return "push-to-main"
    if re.search(
        _GIT_PREFIX + r"push\s+(?:-\S+\s+){0,%d}\S+\s+(?:main|master)(?!:)\b" % _MAX_GLOBAL_FLAG_TOKENS,
        command,
    ):
        return "push-to-main"
    # API-based ref-mutation forms (gh api / curl / wget targeting
    # /git/refs with mutating HTTP methods) classify by HTTP semantic:
    # DELETE → branch-delete class (removes a ref)
    # PATCH/POST/PUT → force-push class (rewrites a ref without PR review)
    # Symmetric with how a force-push or branch-delete token from
    # extract_context() would authorize the equivalent CLI form.
    # gh-api recognition uses the TOLERANT `_GH_API_PREFIX` (same as the read floor)
    # so a `gh -R o/r api ...` global-flag spelling MINTS instead of gating-on-read-
    # but-not-minting (#1064 over-block). The METHOD checks below are IGNORECASE to
    # match the IGNORECASE read floor, so a lowercase `-X delete` faithful form MINTS
    # too. Both are mint-WIDENING (the read floor already gates these forms) — never a
    # read-floor narrowing. Still gh-api/curl/wget only (NOT httpie), so no new
    # gated-but-unmintable httpie state — and httpie now has NO read-floor arms at all
    # (the read floor dropped them; httpie is wholly out of charter), so the invariant
    # is two-sided.
    _is_api_form = (
        re.search(_GH_API_PREFIX, command, re.IGNORECASE)
        or re.search(r"\b(?:curl|wget)\b", command, re.IGNORECASE)
    )
    if _is_api_form and "git/refs" in command:
        if re.search(r"\bDELETE\b", command, re.IGNORECASE):
            return "branch-delete"
        if re.search(r"\b(?:PATCH|POST|PUT)\b", command, re.IGNORECASE):
            return "force-push"
    # branch-protection API mutation (#1063): DELETE|PUT|PATCH on a
    # `branches/<b>/protection` endpoint WEAKENS protection (remove / replace /
    # modify) — a DISTINCT op-class from branch-delete (it changes a config, not a
    # ref). POST is EXCLUDED (it ENABLES protection sub-features = strengthening, so
    # gating it would over-block). Path-disjoint from the git/refs arm above (a
    # protection URL has no `git/refs`), so no shadowing. Method check is loose +
    # IGNORECASE like the git/refs arm; the GAP1 write-gate (is_dangerous + the precise
    # method-gated DANGEROUS_PATTERNS arms) ensures mint⊆read.
    if _is_api_form and re.search(r"branches/.*/protection", command):
        if re.search(r"\b(?:DELETE|PUT|PATCH)\b", command, re.IGNORECASE):
            return "branch-protection"
    # API-based PR merge (#1096, GH-API-ONLY per Option B): gh api with a mutating method
    # (PUT/PATCH/POST) on a pulls/<N>/merge endpoint is a merge via API. curl/wget were
    # DROPPED (sec #71: unsound value-flag denylist over an unbounded flag space) — they
    # classify None and stay gated-but-unmintable. Recognized AND extracted by
    # the ONE shared per-leg helper _api_merge_leg_endpoint, so recognition<->extractability
    # read the SAME surface — a leg classifies merge IFF its ENDPOINT-position PR is
    # extractable, which gives no gated-but-unmintable AND binds the URL-positional
    # endpoint (never a flag-value / other-leg decoy — the #1096 target-confusion fix).
    # ADDITIVE: this classifies inputs that were previously None (a genuine API merge)
    # while leaving every EXISTING classification byte-identical. (It is NOT true that
    # "every currently-None input stays None" — a genuine api-merge is correctly
    # None->merge; that is the whole point of the arm. Only NON-merge inputs are
    # unchanged.) Recognition predicate: tolerant IGNORECASE client (_GH_API_PREFIX, so a
    # `gh -R o/r api` global-flag spelling also mints) + IGNORECASE PUT/PATCH/POST +
    # case-SENSITIVE pulls/<N>/merge path. DELETE excluded; the implicit-POST (-f/--data,
    # no method keyword) spelling is a deliberate residual, per the git/refs detect arm.
    for _leg in legs:
        if _api_merge_leg_endpoint(_leg) is not None:
            return "merge"
    # branch-delete: git branch -D / --delete --force / --force --delete. Matched
    # PER-LEG over the shared _BRANCH_DELETE_LITERAL_ARMS SSOT (#1094) — the same
    # arm tuple the read floor consumes, over the same leg substrate
    # (_split_into_legs), so a stray force-delete token in a benign continuation
    # leg no longer classifies the compound as branch-delete, and read==mint
    # holds by construction on this class (a command is gated via these arms iff
    # some stripped leg matches iff detect classifies branch-delete here).
    # Clustered spellings (-Df / -fD / --delete -f) fall through to the union-arm
    # fallback below, same as the read floor.
    for _leg in legs:
        if any(arm.search(_leg) for arm in _BRANCH_DELETE_LITERAL_ARMS):
            return "branch-delete"
    # Quote-aware normalized-flag FALLBACK (ADDITIVE, INV-AU): catches the
    # clustered/split flag spellings the literal regexes above miss — chiefly
    # `git branch -Df`/`-fD`/`--delete -f` (force-delete), which `-D\b` and the
    # spelled-out `--delete --force` cannot see. Only reached when every literal
    # check above has missed, so it can never override an established op-class
    # precedence; it returns None when no flag-condition fires.
    return _flag_condition_danger_op(command)


# -----------------------------------------------------------------------------
# Command-context extraction — the shared SSOT both hooks call on a COMMAND
# STRING (never prose). The mint side (merge_guard_post) and the read side
# (merge_guard_pre) both derive a command's (operation_type, target) from
# extract_command_context, so the two arms can never classify the SAME command
# differently again (the #720 / asymmetric-classifier bug class). A context key
# is PRESENT only when positively extracted; ABSENT otherwise — absence, NOT a
# None value, is the fail-closed signal a downstream gate keys on.
# -----------------------------------------------------------------------------

# PR-number positional extraction regex.
#
# Both flag-walks (between `gh` and `pr`, AND between the subcommand and the PR
# number) use the tight `_GH_FLAG_TOKENS` form. A broad `_GH_GLOBAL_FLAGS` form
# on the pre-subcommand walk would allow greedy consumption past a `gh pr
# <subcmd> <PR>` substring inside `--body "..."` text, then re-anchor at a
# SECOND `gh pr <subcmd>` occurrence embedded in the body — an authorization
# bypass where the context check matched an embedded fake PR rather than the
# real positional. Restricting both walks to flag-shaped tokens prevents walking
# past the real positional into quoted body content.
#
# The trailing `(?![\w-])` rejects BOTH alphanumeric-suffix tokens (`7352abc`)
# AND hyphen-suffix tokens (`7352-tests`). Python `\b` matches at a digit-to-
# hyphen boundary (`-` is a non-word char), so a plain `\b` would incorrectly
# capture `7352` from `7352-tests` (a branch-name argument). `(?![\w-])` is
# strictly stronger: it rejects any continuation that is a word char OR a hyphen.
_GH_PR_NUMBER_RE = re.compile(
    r"\bgh\s+" + _GH_FLAG_TOKENS + r"pr\s+(?:merge|close)\s+"
    + _GH_FLAG_TOKENS + r"(\d+)(?![\w-])"
)

# A quoted-command region inside prose: backticks (most common), then single
# quotes, then double quotes; captures the content. When AskUserQuestion text
# embeds the literal command in a quoted region (e.g. `gh pr merge 42`), the
# SAME classifier the read side uses is applied to the embedded command,
# guaranteeing bidirectional write/read agreement on the SAME input.
_QUOTED_COMMAND_RE = re.compile(
    r"`([^`]+)`"        # backticks
    r"|'([^']+)'"       # single quotes
    r'|"([^"]+)"'       # double quotes
)

# A bare (unquoted) `gh ...` / `git ...` command span: from the tool name up to
# a shell separator (`;` `|` `&`), a quote, or end-of-line. The conservative
# extractors below filter prose-polluted spans (a span that yields an op but no
# target contributes no (op,target) pair), so over-capturing trailing prose is
# harmless — it never invents a target.
_BARE_COMMAND_RE = re.compile(r"\b(?:gh|git)\s+[^`'\";|&\n]+")

# Allowlist of `gh pr merge|close` long-form flags KNOWN to take a value. The
# defensive check in _extract_pr_number only rejects digits preceded by one of
# these value-taking flags (avoiding false-positives on value-less flags like
# `--admin`, `--auto`, `--squash` whose positional digit IS the PR). As of `gh`
# v2 no real flag takes a digit value; this is a forward-compatible defense.
# Extend this list when `gh` ships a flag that takes a numeric value.
_GH_PR_VALUE_TAKING_FLAGS = frozenset({
    "--body",
    "--body-file",
    "--subject",
    "--author-email",
    "--match-head-commit",
    "--comment",
    "--max-retries",
    "--retry-count",
    "--timeout",
})


def _strip_surrounding_quotes(token: str) -> str:
    """Strip one layer of matching surrounding quotes from a captured CLI token.

    ``'feat/x'`` -> ``feat/x``, ``"feat/x"`` -> ``feat/x``. Leaves an unquoted or
    mismatched-quote token unchanged. Comparison-side normalization only — it
    does NOT widen what a matcher regex captures, so it cannot introduce a
    false-negative.
    """
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    return token


def _extract_pr_number(command: str) -> str | None:
    """Extract the PR number positional from a `gh pr merge|close` command.

    Wraps `_GH_PR_NUMBER_RE.search()` with a defensive post-extract check that
    rejects digits which are actually the VALUE of an immediately-preceding
    value-taking long-form flag (e.g. `--max-retries 5`). Value-less flags
    (`--admin`, `--auto`, `--squash`) do NOT trigger the check — a digit after
    one of them IS the PR positional. Returns None when no positional is found.
    """
    match = _GH_PR_NUMBER_RE.search(command)
    if not match:
        return None
    pr_pos = match.start(1)
    # Inspect the immediately-preceding token for a known value-taking
    # long-form flag; if present, the captured digit is its value, not the PR.
    preceding = command[:pr_pos].rstrip()
    flag_match = re.search(r"(--[\w-]+)$", preceding)
    if flag_match and flag_match.group(1) in _GH_PR_VALUE_TAKING_FLAGS:
        return None
    return match.group(1)


def _extract_api_ref(command: str) -> str | None:
    """Parse the ref from an API ref-mutation command's `git/refs/<ref>` path.

    `detect_command_operation_type` classifies `gh api|curl|wget` calls on a
    `git/refs/...` path by HTTP method (DELETE -> branch-delete, PATCH/POST/PUT
    -> force-push). For both classes the affected ref is the path component, so
    a single parser supplies the target. Returns the ref (a leading `heads/`
    stripped), or None when the command is not a recognized API ref form.
    """
    if not (
        re.search(r"\b(?:gh\s+api|curl|wget)\b", command, re.IGNORECASE)
        and "git/refs/" in command
    ):
        return None
    api_match = re.search(
        r"git/refs/(?:heads/)?([A-Za-z0-9][A-Za-z0-9._/-]*)", command
    )
    return api_match.group(1) if api_match else None


# Value-taking-flag skip set for the gh-api api-merge endpoint walk (#1096; GH-API-ONLY
# per Option B). The api-merge mint arm is gh-api ONLY — curl/wget were DROPPED because
# sec #71 proved a value-flag denylist over curl/wget's UNBOUNDED flag space is
# structurally uncompletable (52 realistic-common decoy leaks), so a curl/wget mint arm
# built on it is unsound. gh api's flags are FINITE + documented + first-positional-endpoint
# = provably sound. Consequence: curl/wget api-merge classifies detect=None (mint=0),
# staying in its PRE-EXISTING gated-but-unmintable state (the read arms still gate it) —
# killing that laundering class BY CONSTRUCTION (can't mint -> can't launder). Tokens are
# the shlex form. Includes the -R/--repo GLOBAL flag, which is decoy-capable (`gh -R
# pulls/5/merge api …` would bind 5 if -R's value is not skipped). Booleans (-i/--include,
# --paginate, --slurp, --silent, --verbose) are DELIBERATELY absent — skipping their next
# token would swallow a positional.
_API_MERGE_GH_VALUE_FLAGS = frozenset({
    "-X", "--method", "-f", "--field", "-F", "--raw-field", "--input",
    "-H", "--header", "--hostname", "-q", "--jq", "-t", "--template",
    "--cache", "-R", "--repo",
})

_PULLS_MERGE_PR_RE = re.compile(r"pulls/(\d+)/merge\b")


def _api_merge_leg_endpoint(leg: str) -> str | None:
    """Return the ENDPOINT PR of a genuine per-leg API merge, or None (#1096).

    ONE shared helper called by BOTH detect_command_operation_type's merge arm AND
    _extract_api_merge_pr, so recognition and extraction read the SAME surface (kills
    the reuse-across-two-domains root: detect recognizes a leg IFF its endpoint is
    extractable -> no gated-but-unmintable BY CONSTRUCTION, and the bound PR is the URL
    POSITIONAL endpoint, never a flag-value / other-leg decoy).

    GH-API-ONLY (#1096 Option B; curl/wget dropped, sec #71). Recognition (gh-api client +
    mutating method + pulls/<N>/merge path co-occur in this leg): tolerant IGNORECASE
    _GH_API_PREFIX client + IGNORECASE PUT/PATCH/POST method + case-SENSITIVE pulls/<N>/merge
    path. A curl/wget merge classifies None here -> gated-but-unmintable (read arms still gate).

    Extraction binds the pulls/<N>/merge that is the URL POSITIONAL: strip body values
    (carrier-8), shlex-tokenize, then walk skipping flags + the gh-api value-flag
    values, and take the first surviving positional. On tokenizer failure NEVER returns
    None for a recognized leg (falls back to the stripped/raw first-match) — a mis-parse
    must not gate a faithful merge (over-block-safe).
    """
    client_gh = re.search(_GH_API_PREFIX, leg, re.IGNORECASE)
    if not client_gh:
        # GH-API-ONLY (#1096 Option B): curl/wget api-merge legs classify None here
        # (mint=0), staying in their PRE-EXISTING gated-but-unmintable state (the
        # curl/wget merge READ arms still gate them) — killing the 52 curl/wget
        # laundering vectors BY CONSTRUCTION (sec #71: their value-flag denylist over an
        # unbounded flag space is uncompletable). No new over-block (status quo ante).
        return None
    # Recognition uses the EXACT non-capturing literal `pulls/\d+/merge\b` via re.search
    # (byte-identical to the prior detect arm), NOT the compiled capturing _PULLS_MERGE_PR_RE
    # — so recognition stays byte-identical AND the non-vacuity arm-disable shim (which
    # monkeypatches re.search of this exact literal) still surgically disables the arm.
    # The capturing _PULLS_MERGE_PR_RE is used only for EXTRACTION (the walk + fallback).
    if not re.search(r"pulls/\d+/merge\b", leg):
        return None
    if not re.search(r"\b(?:PUT|PATCH|POST)\b", leg, re.IGNORECASE):
        return None
    # Recognized. Extract the ENDPOINT-position PR.
    stripped = _strip_non_executable_content(leg)
    tokens = _shell_tokenize(stripped)
    if tokens is None:
        # Tokenizer failure (unbalanced quotes): NEVER None for a recognized leg
        # (over-block-safe). Prefer the body-stripped surface (avoids body decoys);
        # the raw leg is guaranteed to match (recognition required it).
        m = _PULLS_MERGE_PR_RE.search(stripped) or _PULLS_MERGE_PR_RE.search(leg)
        return m.group(1) if m else None
    value_flags = _API_MERGE_GH_VALUE_FLAGS
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            base = tok.split("=", 1)[0]
            if "=" not in tok and base in value_flags:
                i += 1  # bare space-separated value-flag: skip its value token too
            # attached-value (`--method=PUT`, `-XPUT`) and booleans consume no extra token
            i += 1
            continue
        # A positional: the endpoint is the FIRST pulls/<N>/merge positional (the URL
        # argument; for gh api the endpoint is the first positional by REST convention).
        mm = _PULLS_MERGE_PR_RE.search(tok)
        if mm:
            return mm.group(1)
        i += 1
    # Recognized (gh-api) but no endpoint positional survived (the only pulls/<N>/merge was
    # a skipped flag VALUE, i.e. a body/decoy — the leg has no real merge endpoint). NOT a
    # tokenizer failure, so this is a genuine "no extractable endpoint" -> None. Correct
    # (no wrong-target mint); such an input never faithfully merges a PR. (#1096 is
    # gh-api-only per Option B: curl/wget never reach this walk — they classify None at
    # the client gate above — so there is no exotic-curl residual to accept.)
    return None


def _extract_api_merge_pr(command: str) -> str | None:
    """Command-level api-merge PR: walk the legs and return the FIRST recognized leg's
    ENDPOINT-position PR (#1096). Leg-scoping closes the cross-leg/echo decoy; the
    per-leg helper closes the same-leg flag-value decoy. Consumed by extract_command_context
    (mint + read whole-fallback + #1097 retirement cmd_target all heal at this one site)."""
    for _leg in _split_into_legs(command):
        pr = _api_merge_leg_endpoint(_leg)
        if pr is not None:
            return pr
    return None


def _extract_protection_branch(command: str) -> str | None:
    """Parse the protected branch from a branch-protection API command's
    `branches/<branch>/protection` path (#1063). The branch is PATH-resident (the
    REST resource IS the URL), so carrier-8's body strip never removes it. The
    non-greedy `(.+?)` correctly handles a slashed branch name (`feature/x`):
    `branches/feature/x/protection` → `feature/x`. Returns the branch, or None when
    the command is not a recognized protection form."""
    m = re.search(r"branches/(.+?)/protection\b", command)
    return _strip_surrounding_quotes(m.group(1)) if m else None


def _extract_branch_name(command: str) -> str | None:
    """Extract the SINGLE branch name targeted by a branch-delete command.

    Owns the branch-delete target for extract_command_context. Handles the CLI
    `git branch -D|--delete <name>` form (exactly ONE branch — a MULTI-target
    delete like `git branch -D a b` is REFUSED, returning None) and the API
    ref-DELETE form (the ref in a `git/refs/<ref>` path). Returns the
    (quote-normalized) name, or None when no single branch target is positively
    extractable.
    """
    api_ref = _extract_api_ref(command)
    if api_ref is not None:
        return api_ref
    # CLI `git branch -D|--delete <name>`: isolate the tokens after `branch`,
    # drop dash-flags, and require EXACTLY ONE positional branch name. A
    # multi-target delete (`git branch -D a b`) has >1 positional -> REFUSE, so
    # a token approved for ONE branch can never authorize deleting several (the
    # #1032 multi-target under-block); 0 positionals -> REFUSE. Mirrors
    # _extract_force_push_target_ref's multi-ref conservatism. The caller only
    # reaches here when detect_command_operation_type already classified the
    # command branch-delete, so a -D/--delete flag is present and is dropped
    # with the other dash-flags.
    # Truncate at the first benign continuation / redirect on the quote-masked
    # view BEFORE counting positionals, so a faithful single branch-delete with a
    # trailing continuation (`git branch -D feature | tail`, `... ; echo done`,
    # `... > log`) re-derives its one branch name instead of miscounting the
    # continuation tokens as extra positionals (-> None -> over-block). An
    # ambiguous quote state makes _executable_prefix return None -> abstain -> the
    # existing safe over-block (never a silently-authorized malformed command).
    prefix = _executable_prefix(command)
    if prefix is None:
        return None
    branch_match = re.search(_GIT_PREFIX + r"branch\b(.*)$", prefix)
    if not branch_match:
        return None
    positionals = [t for t in branch_match.group(1).split() if not t.startswith("-")]
    if len(positionals) != 1:
        return None
    return _strip_surrounding_quotes(positionals[0])


def _canonical_join(items: Sequence[str]) -> str:
    """Netstring/length-prefix encode a sequence of strings into ONE canonical
    identity STRING, injective by construction (a left-inverse decoder exists), so
    distinct sequences never collide REGARDLESS of item content — incl. commas, '@',
    '#', ':' and NUL. Pure + deterministic (no sort, no dedup — the caller supplies a
    canonical ordered list). Stays a `str` (the token persists as JSON; compared by
    str()-equality, never decoded). Shared SSOT for branch_set + mass_target (#1136)."""
    return "".join(f"{len(i)}:{i}" for i in items)


def _extract_branch_delete_set(command: str) -> str | None:
    """Extract the canonical MULTI-branch identity of a force-delete command.

    The MULTI-target sibling of _extract_branch_name (which owns the SINGLE
    branch). Handles the CLI `git branch -D|--delete --force <a> <b> ...` form
    with TWO OR MORE positional branch names, returning a canonical
    sort+dedup+quote-strip identity STRING (the shared netstring `_canonical_join`
    SSOT), or None
    when fewer than two branch names are positively extractable (<2 -> defers to
    the scalar _extract_branch_name path — the BOUNDARY discriminator, so a
    command populates EXACTLY ONE of `branch` / `branch_set`).

    Shares the _extract_mass_delete_target encoder (#1062b/#1136): an
    ORDER-INDEPENDENT identity STRING (never a tuple/list — a token persists as
    JSON, and the read side compares via `str()`, so a list<->tuple round-trip
    must never enter the identity), built + canonicalized in this ONE shared SSOT
    so mint and read derive byte-identical strings (D2 symmetry by construction).
    Encoding is the netstring `_canonical_join` (`len:name` framing): the identity
    is INJECTIVE by construction and CONTENT-AGNOSTIC, so distinct branch SETS never
    collide regardless of name content — no separator has to be ref-illegal. (A bare
    `,` join was NOT injective: a branch literally named `a,b` collided with the set
    {a, b}, cross-authorizing distinct sets — the F1 review finding; framing closes
    that class outright. The string is JSON/str()-safe: json round-trips it and str()
    is stable, so the identity survives token persistence.)
    Set-EQUALITY on this INJECTIVE string then closes the #1032 multi-target
    under-block UNCONDITIONALLY: a `{a,b}` token cannot authorize `{a,b,c}`
    (unequal strings) while a `{b,a}` reorder MATCHES (both canonicalize to the
    same string).

    Uses the SAME tokenization as _extract_branch_name (executable-prefix
    truncation at a benign continuation/redirect via _executable_prefix, then drop
    dash-flags), so the single/multi boundary is computed IDENTICALLY and no
    command is double-counted or dropped. FORCE-only rides on op_type: the caller
    reaches here only when detect_command_operation_type already classified
    branch-delete (the FORCE `-D`/`-Df`/`-fD`/`--delete --force` spellings; #1094
    per-leg cure), so a lowercase `-d`/`--delete` merged-branch delete (ungated at
    HEAD) never reaches this and never populates `branch_set`.
    """
    prefix = _executable_prefix(command)
    if prefix is None:
        return None
    branch_match = re.search(_GIT_PREFIX + r"branch\b(.*)$", prefix)
    if not branch_match:
        return None
    positionals = [t for t in branch_match.group(1).split() if not t.startswith("-")]
    if len(positionals) < 2:
        return None
    names = sorted({_strip_surrounding_quotes(t) for t in positionals})
    # Encode via the shared netstring SSOT (_canonical_join): each name is framed as
    # `len:name`, so the identity is INJECTIVE by construction and CONTENT-AGNOSTIC —
    # distinct sets never collide regardless of name content, without depending on any
    # separator being ref-illegal (an earlier bare `,` join collided a branch named
    # `a,b` with the set {a, b}). JSON/str()-safe, so mint==read symmetry and the JSON
    # token round-trip both hold.
    return _canonical_join(names) or None


def _extract_force_push_target_ref(command: str) -> str | None:
    """Conservative force-push destination-ref parse (KD-6) — refuse on ambiguity.

    Returns the ref a force-push would rewrite, or None when the target is
    implicit / multi-ref / unparseable (the caller treats None as ABSENT ->
    REFUSE, the safe over-block direction). The accepted ref-form set is
    SECURITY-RATIFICATION-PENDING (ratified at peer-review); this is the
    architect's conservative default.

    Recognized:
        gh api|curl|wget .../git/refs/<ref>   -> <ref>    (API ref-mutation)
        git push <remote> <src>:<dst>         -> <dst>
        git push <remote> HEAD:<dst>          -> <dst>
        git push <remote> <branch>            -> <branch> (incl. direct-to-main)
    Refused (-> None):
        git push --force            (implicit current-branch target)
        git push <remote>           (remote-only, implicit branch)
        any multi-ref / chained / value-flag-ambiguous / unparseable form
    """
    # API ref-mutation: the destination ref is in the git/refs/<ref> path.
    api_ref = _extract_api_ref(command)
    if api_ref is not None:
        return api_ref

    # CLI push: isolate the token sequence after `push`, drop dash-flags, and
    # require EXACTLY remote + refspec (2 positionals). 0 = implicit push; 1 =
    # remote-only (implicit branch); >2 = multi-ref/chained -> all ambiguous,
    # REFUSE. A value-taking dash-flag (e.g. `-o opt`) shifts the positional
    # count off 2 -> also refused (conservative over-block).
    # Truncate at the first benign continuation / redirect on the quote-masked
    # view BEFORE counting positionals, so a faithful single force-push with a
    # trailing continuation (`git push --force origin main | tail`, `... 2>&1`,
    # `... > log`, `... && echo done`) re-derives its target instead of
    # miscounting the continuation tokens as extra positionals (-> None ->
    # over-block). The redirect filename is structurally outside the positional
    # window (`... feature > main` yields `feature`, never `main`). An ambiguous
    # quote state makes _executable_prefix return None -> abstain -> the existing
    # safe over-block (never a silently-authorized malformed command).
    prefix = _executable_prefix(command)
    if prefix is None:
        return None
    push_match = re.search(_GIT_PREFIX + r"push\b(.*)$", prefix)
    if not push_match:
        return None
    positionals = [t for t in push_match.group(1).split() if not t.startswith("-")]
    if len(positionals) != 2:
        return None
    refspec = _strip_surrounding_quotes(positionals[1])
    if ":" in refspec:
        return refspec.rsplit(":", 1)[1] or None
    return refspec or None


# git-push value-taking OPTION flags whose VALUE token must be skipped when
# counting refspec positionals (else a contrived `-o ':weird'` push-option leaks
# a fake delete refspec — the #1037 brittleness class). Their `--flag=value` form
# carries the value INLINE (one token), so no next-token skip is needed. Used by
# the remote-ref-delete + remote-mass-delete positional builder below; distinct
# from the OP-TRIGGER flags (`--delete`/`-d`) which are NOT value-taking (the ref
# is a separate positional).
_GIT_PUSH_VALUE_FLAGS = frozenset(
    {"-o", "--push-option", "--receive-pack", "--exec", "--repo"}
)


def _push_positionals(after_push: list[str]) -> list[str]:
    """Positional (non-flag) tokens after the `push` subcommand, skipping the value
    token consumed by a git-push value-taking option flag (`-o opt`, `--repo url`,
    …). A `--flag=value` form carries its value inline (one token), so only the
    separate-token form skips a follow-on token. Operates on a quote-aware
    `_shell_tokenize` view (quotes already stripped), so a quoted `':oldref'` stays
    ONE token bound to its flag and never leaks as a positional. Shared by both
    push-delete extractors so they agree on positional boundaries."""
    positionals: list[str] = []
    i, n = 0, len(after_push)
    while i < n:
        tok = after_push[i]
        if tok.startswith("-") and tok != "-":
            flag = tok.split("=", 1)[0]
            if flag in _GIT_PUSH_VALUE_FLAGS and "=" not in tok and i + 1 < n:
                i += 2  # consume the option flag's separate value token
                continue
            i += 1  # a flag (boolean, op-trigger, or inline-value) — not a positional
            continue
        positionals.append(tok)
        i += 1
    return positionals


def _tokens_after_push(command: str) -> list[str] | None:
    """The quote-aware token list AFTER the first `push` token, on the executable
    prefix of `command`, or None when the command is unparseable / has no `push`
    token. Truncates at the first benign continuation / redirect (via
    `_executable_prefix`) and abstains (None) on ambiguous quotes / procsub —
    fail-OPEN to the literal floor, never fail-closed."""
    prefix = _executable_prefix(command)
    if prefix is None:
        return None
    toks = _shell_tokenize(prefix)
    if toks is None:
        return None
    for idx, tok in enumerate(toks):
        if tok == "push":
            return toks[idx + 1:]
    return None


def _extract_remote_ref_delete_target(command: str) -> str | None:
    """Extract the SINGLE remote ref a `git push` delete would remove (#1062a), or
    None when the form is implicit-current/multi-ref/ambiguous (→ the caller defers:
    not recognized as remote-ref-delete; a mass form is picked up by
    `_extract_mass_delete_target` instead). Reuses the force-push parser MACHINERY
    (quote-mask + shlex view + value-flag skip) but adapts the positional rule for
    delete semantics + the implicit-remote forms the force-push parser returns None
    for (its conservative exactly-2-positional rule). Recognition⟺mintability by
    construction: the `_flag_condition_danger_op` arm returns `remote-ref-delete`
    IFF this yields a target, so the op can never reach a #1064 gated-but-unmintable
    state.

    Recognized (git grammar `git push [<repo>] <refspec>...`):
        explicit remote, --delete/-d:  `origin --delete feature` → feature
        implicit remote, --delete/-d:  `git push --delete feature` → feature
        single-delete-with-repo:       `git push --delete a b` → b (repo=a, ref=b)
        empty-source colon refspec:    `origin :feature` / `origin +:feature` → feature
                                       `origin :refs/tags/v1` → refs/tags/v1
    Deferred (→ None, picked up as remote-mass-delete or not destructive):
        multi-ref delete (`origin --delete a b`, `origin :a :b`), --mirror/--prune,
        plain push (`origin feature`), src:dst non-delete (`origin feat:feat`),
        value-flag colon (`-o ':weird' main`), a delete literal inside a quoted arg.
    """
    after = _tokens_after_push(command)
    if after is None:
        return None
    positionals = _push_positionals(after)
    # CROSS-LEG FIX (review FINDING 1): compute the flag set from the SAME leg as the
    # positionals — the post-`push` tokens of the executable prefix (`after`), NOT the
    # whole command. Else a --delete/-d/--mirror/--prune token in a benign CONTINUATION
    # leg (`git push origin feature && git branch -d old`) leaks in and mislabels the
    # benign push as a delete (a fail-safe but common over-block). Every delete-relevant
    # flag is a push SUBCOMMAND option (always after `push`), so the post-push tokens are
    # the complete + correct scope; nothing real is missed (no read-floor narrowing).
    gf = _normalized_flags(after, "git")  # git surface maps -d → --delete
    if "--delete" in gf:
        # git grammar: 1 positional = the refspec (implicit remote); 2 = <repo>
        # <refspec>; 0 or ≥3 = ambiguous/multi → defer (mass handles ≥3).
        if len(positionals) == 1:
            return _strip_surrounding_quotes(positionals[0]) or None
        if len(positionals) == 2:
            return _strip_surrounding_quotes(positionals[1]) or None
        return None
    # No --delete flag: an empty-source colon refspec (`:dst` / `+:dst`) is a delete.
    # EXACTLY ONE → its dst; ≠1 → defer (0 = not a delete; ≥2 = multi → mass).
    colon_dsts = [p for p in positionals if re.match(r"^\+?:", p)]
    if len(colon_dsts) == 1:
        return _strip_surrounding_quotes(colon_dsts[0]).rsplit(":", 1)[1] or None
    return None


# Sentinel marking an IMPLICIT remote (a `git push --mirror` with no positional
# remote) in a mass-delete target tuple — keeps the implicit-remote form MINTABLE
# (a definite, deterministic target) rather than ambiguous. A NUL byte can never
# appear in a real remote name, so it can never collide with one.
_IMPLICIT_REMOTE_MARKER = "\x00implicit"


def _extract_mass_delete_target(command: str) -> str | None:
    """Extract a READABLE normalized per-invocation tuple binding the destructive
    IDENTITY of a `git push` MASS-delete form (#1062b — `--mirror`/`--prune`/multi-ref
    delete), or None when the command is not such a form. Binds the destructive
    identity (mass-flags + remote + sorted refspecs), NOT the whole command line, so a
    benign `-o ci.skip` does not over-bind; privileged flags ride the existing #1042
    `bound_flags` axis. Derived identically on BOTH arms via this ONE SSOT → mint==read
    parity by construction; recognition⟺mintability (the arm returns the op IFF this is
    non-None) → #1064-impossible. Returns a READABLE netstring identity STRING
    (`len:value`-framed field tuple; no `@`/`#`/`,` delimiters; #1136), never a hash:

        _canonical_join([<sorted-mass-flags>, <remote-or-implicit-marker>, *<sorted-deduped-refspecs>])
        git push --mirror origin               → 8:--mirror6:origin
        git push --mirror                      → 8:--mirror9:\\x00implicit (implicit-remote MINTABLE)
        git push --prune origin refs/heads/main → 7:--prune6:origin15:refs/heads/main
        git push origin --delete a b           → 8:--delete6:origin1:a1:b (binds the EXACT deleted set)
        git push origin :a :b                  → 8:--delete6:origin1:a1:b

    BOUNDARY (lead-mandated, no double-classification): a SINGLE-ref delete is owned by
    `_extract_remote_ref_delete_target` (tried FIRST in the recognition arm). This
    extractor returns None for a single-ref form, so a command is classified by EXACTLY
    ONE op-class. Per git grammar (first positional = repository), `git push --delete a b`
    is repo=a/ref=b = SINGLE (→ remote-ref-delete), while `git push origin --delete a b`
    is repo=origin/refs=a,b = MASS.
    """
    if not re.search(_GIT_PREFIX + r"push\b", command):
        return None
    # Single-ref delete is the OTHER op-class — defer to it (the boundary discriminator).
    if _extract_remote_ref_delete_target(command) is not None:
        return None
    after = _tokens_after_push(command)
    if after is None:
        return None
    # CROSS-LEG FIX (review FINDING 1): flag set from the SAME leg as the positionals
    # (the post-`push` tokens), NOT the whole command — else a --mirror/--prune/--delete
    # in a benign continuation leg (`git push origin feature && echo --mirror`) leaks in
    # and mislabels the benign push as a mass-delete. All mass-relevant flags are push
    # subcommand options, so post-push is the complete + correct scope.
    gf = _normalized_flags(after, "git")  # needs --mirror/--prune in _FLAG_SPEC (added #1062b)
    mass_flags = sorted(f for f in ("--mirror", "--prune") if f in gf)
    positionals = _push_positionals(after)
    colon_dsts = [p for p in positionals if re.match(r"^\+?:", p)]

    if mass_flags:
        # --mirror/--prune: remote = first positional (git grammar) or implicit;
        # explicit refspecs (if any) are the remaining positionals.
        remote = _strip_surrounding_quotes(positionals[0]) if positionals else _IMPLICIT_REMOTE_MARKER
        refspecs = sorted(_strip_surrounding_quotes(p) for p in positionals[1:])
        flags_part = ",".join(mass_flags)
    elif "--delete" in gf:
        # multi-ref --delete (the single-ref case already deferred above): git grammar
        # repo = first positional, the rest are the deleted refs. Need >=2 refs to be mass.
        if len(positionals) < 3:
            return None
        remote = _strip_surrounding_quotes(positionals[0])
        refspecs = sorted(_strip_surrounding_quotes(p) for p in positionals[1:])
        flags_part = "--delete"
    elif len(colon_dsts) >= 2:
        # multi empty-source colon delete (`origin :a :b`): remote = first non-colon
        # positional (or implicit); refs = the colon dsts.
        non_colon = [p for p in positionals if not re.match(r"^\+?:", p)]
        remote = _strip_surrounding_quotes(non_colon[0]) if non_colon else _IMPLICIT_REMOTE_MARKER
        refspecs = sorted(
            _strip_surrounding_quotes(p).rsplit(":", 1)[1] for p in colon_dsts
        )
        flags_part = "--delete"
    else:
        return None

    # Encode the destructive identity via the shared netstring SSOT (_canonical_join):
    # framing subsumes the old `@`/`#`/`,` delimiters, so the identity is injective for
    # ANY field content (closes the #1136 `#`/comma collision class). Dedup refspecs
    # (a duplicate ref is the same target); empty refspecs frame uniformly (no `#`).
    return _canonical_join([flags_part, remote, *sorted(set(refspecs))])


# -----------------------------------------------------------------------------
# Privileged-flag binding (#1042). The (operation_type, target) binding above
# DROPS every dash-flag, so an approved `gh pr merge 5` and an executed
# `gh pr merge 5 --admin` (branch-protection bypass) reduce to the SAME context
# and authorize — the flag rides past the checkpoint undetected. The fix adds
# ONE more binding dimension — `bound_flags` — computed by the SINGLE scanner
# below, called from the SINGLE site in extract_command_context, so BOTH hook
# arms inherit it and can never classify a command's flags differently (the same
# anti-drift property that the shared (op,target) SSOT already guarantees).
#
# PRIVILEGED_FLAGS is the op-class-scoped denylist: { op_type -> { canonical_long
# -> (aliases, value_taking) } }. Membership is PURE DATA — adding or removing a
# flag is a one-line edit with ZERO scanner/predicate changes, so the security
# review owns membership without touching logic. A flag's PRESENCE binds it; the
# read-side set-equality gate then enforces never-escalate.
#
# EXCLUDES op-trigger flags that already change op_type (and are therefore
# already bound through it): --force/-f (force-push), -D (branch-delete), and
# gh pr close's --delete-branch (the close-danger trigger). Listing them here
# would double-bind and needlessly over-block. NB the asymmetry: --delete-branch
# /-d on gh pr MERGE is a post-merge SIDE-EFFECT (deletes the source branch), not
# a merge op-trigger, so it IS bound on the `merge` class — and -d (merge
# delete-branch) is a DIFFERENT op from -D (branch force-delete); op-class scoping
# keeps them from being conflated.
PRIVILEGED_FLAGS: dict[str, dict[str, tuple[tuple[str, ...], bool]]] = {
    "merge": {
        "--admin":         (("--admin",), False),               # bypass branch protection
        "--delete-branch": (("-d", "--delete-branch"), False),  # side-effect: deletes source branch
        "--repo":          (("-R", "--repo"), True),            # cross-repo redirect (value-carrying target)
        # value-carrying SAFETY constraint (pins the merge to a head SHA); binding
        # it closes the dropped-constraint case — approve with --match-head-commit,
        # execute without it -> set-equality REFUSES (#1042).
        "--match-head-commit": (("--match-head-commit",), True),
    },
    "close": {
        "--repo":          (("-R", "--repo"), True),            # cross-repo redirect (value-carrying target)
        # --delete-branch/-d: the IRREVERSIBLE branch-deleting variant of close. Bound
        # here (symmetry with merge above) so a bare-close token's flag-set can never
        # set-equal the --delete-branch variant → the bare→delete-variant escalation
        # REFUSES. NB it is ALSO a close op-trigger, but op_type folds bare-close and
        # close --delete-branch into the SAME 'close' op (close precedence), so the
        # trigger alone does NOT distinguish the two commands at the bind layer — this
        # flag binding is what separates them. No double-bind: op-trigger sets the OP
        # dimension, this sets the orthogonal FLAG-SET dimension.
        "--delete-branch": (("-d", "--delete-branch"), False),
    },
    "force-push": {
        "--no-verify":     (("--no-verify",), False),           # bypass pre-push hook
    },
    "push-to-main": {
        # --force-with-lease: the lease push CAN rewrite history (unlike a plain push),
        # so its PRESENCE separates plain-push and lease-push token identities inside
        # one op-class (the close/--delete-branch precedent above). Bound as a BOOLEAN:
        # git's value is =-joined only (never space-separated), and a value-taking
        # marking would (i) consume the next positional (`origin`) on the bare spelling
        # and (ii) import mint-side adjacency-sensitivity from the wide flag_scan_text
        # surface -> an over-block risk. All =<ref>:<expect> spellings therefore bind
        # the same canonical bare token; intra-lease value variation is an accepted
        # residual (never authorizes plain<->lease or lease<->force escalation).
        "--force-with-lease": (("--force-with-lease",), False),
    },
    "branch-delete": {
        # No bound flags today: branch-delete's privileged effect is its op-trigger
        # (-D / --delete --force), already bound via op_type. Kept as an explicit
        # extension point so a future bound flag is a one-line data edit here.
    },
    "remote-ref-delete": {
        # No bound flags: remote-ref-delete's privileged effect (removing a remote
        # ref) IS its op-trigger (--delete/-d/empty-source colon), already bound via
        # op_type. Empty entry = explicit #1042 extension point (a future bound flag
        # is a one-line data edit); it adds NO new bound flag, so the set-equality
        # bind is untouched.
    },
    "remote-mass-delete": {
        # No bound flags: remote-mass-delete's privileged effect (the mass destructive
        # push) IS its op-trigger (--mirror/--prune/multi-ref-delete), bound via op_type
        # AND folded into the mass_target identity tuple. The --mirror/--prune additions
        # go to _FLAG_SPEC (danger-condition recognition) ONLY, NOT here, so the #1042
        # set-equality bind is untouched. Empty entry = explicit extension point.
    },
    "branch-protection": {
        # No bound flags: branch-protection's privileged effect (weakening protection)
        # IS its op-trigger (the DELETE|PUT|PATCH method on the protection endpoint),
        # bound via op_type. Empty entry = explicit #1042 extension point; adds NO new
        # bound flag, so the set-equality bind is untouched.
    },
}


def extract_privileged_flags(command: str, op_type: str | None) -> list[str]:
    """Scan a command for the privileged dash-flags bound on its op-class (#1042).

    Returns a SORTED list of canonical flag tokens (boolean flags as their
    canonical long form, e.g. ``--admin``; value-taking flags as
    ``--repo=<value>``). The read side compares these as SETS for exact equality,
    so any added privilege OR dropped constraint mismatches and REFUSES.

    The scan is a SINGLE linear ``str.split()`` token-walk against the op-class
    denylist (``PRIVILEGED_FLAGS``) — constant per-token work, NO regex, no
    backtracking — so it preserves the bounded/linear extraction invariant
    (INV-D2) the rest of this module is careful about.

    Normalizes every CLI form to one canonical token: exact long (``--admin``),
    short alias (``-R`` -> ``--repo``), ``=``-joined (``--repo=x`` / ``-R=x``),
    attached short value (``-Rx`` -> ``--repo=x``), and combined-short clusters
    via a general per-character walk (``-sd`` -> ``--delete-branch``;
    ``-dR owner/repo`` -> ``--delete-branch`` + ``--repo=owner/repo``) so NO bound
    short is ever dropped regardless of cluster ordering. On the GIT surface
    ONLY, an unambiguous long-prefix abbreviation is EXPANDED to its canonical
    flag (``--no-verif`` -> ``--no-verify``) — this is SECURITY-LOAD-BEARING:
    git's parser accepts abbreviation, so a missed match would be a silent
    UNDER-block; gh rejects abbreviation, so its surface needs no expansion.

    Args:
        command: The command (read arm) or full approval surface (mint arm) to
            scan. The caller decides which; the scanner treats it as one string.
        op_type: The classified operation type, or None. Selects the denylist;
            an op_type with no denylist entry (incl. None and the API/un-flagged
            classes) yields ``[]``.

    Returns:
        Sorted list of canonical bound-flag tokens; ``[]`` when none are present.
    """
    denylist = PRIVILEGED_FLAGS.get(op_type) if op_type is not None else None
    if not denylist:
        # op_type is None, unknown, or carries no bound flags (e.g. branch-delete
        # today). An empty result binds the empty set — over-block-safe and the
        # correct outcome for the API/un-flagged classes.
        return []

    # Derive the lookup tables from the denylist ONCE per call. All small,
    # constant-size structures (the denylist has <=3 entries per op-class), so
    # the per-token work below stays O(1).
    alias_to_canonical: dict[str, str] = {}
    value_taking: set[str] = set()
    canonical_long_names: list[str] = []
    for canonical, (aliases, takes_value) in denylist.items():
        canonical_long_names.append(canonical)
        if takes_value:
            value_taking.add(canonical)
        for alias in aliases:
            alias_to_canonical[alias] = canonical
    # git's parse-options expands unambiguous long-prefix abbreviations; gh's
    # pflag rejects them. Only the git surface needs abbreviation expansion.
    # push-to-main is a git surface — DEFENSE-IN-DEPTH: no abbreviated lease
    # spelling reaches this bind in the live flow today. Any prefix still
    # containing `--force` (e.g. `--force-with-leas`) classifies FORCE-PUSH
    # first (the force arms' lookahead excludes only the exact `-with-lease`
    # suffix), and the shorter prefixes that DO classify push-to-main (`--forc`,
    # `--fo`) are ambiguous to git itself — git rejects the command, so no live
    # lease push runs unbound. The expansion keeps the bind correct if either
    # neighbor ever shifts; a unique-in-OUR-denylist prefix binds conservatively
    # (over-block-safe).
    is_git_surface = op_type in ("force-push", "branch-delete", "push-to-main")

    # P1 quote-aware tokenization (closes the quoted-flag bind bypass #3: a
    # `"--admin"` is shlex-stripped to `--admin` → bound; the old `command.split()`
    # kept the quotes → `startswith('-')` skipped it → the escalation rode along).
    # BOTH arms call this shared function so the bind stays symmetric. On an
    # unbalanced quote shlex returns None; fall back to `split()` so the bind never
    # regresses below today's coverage. The bind is defense-in-depth on top of the
    # literal floor (is_dangerous_command), which is the fail-closed default — there is
    # no metachar suppressor in the honest-mistake model.
    tokens = _shell_tokenize(command)
    if tokens is None:
        tokens = command.split()
    found: set[str] = set()
    i = 0
    n = len(tokens)
    while i < n:
        token = tokens[i]
        # Non-flag tokens, the bare `-` (stdin) and `--` (end-of-options) marker
        # never bind. Skipping `--` is load-bearing: it must NOT prefix-match a
        # sole long flag in the abbreviation branch below.
        if not token.startswith("-") or token in ("-", "--"):
            i += 1
            continue

        if token.startswith("--"):
            # Long flag: exact denylist hit, or — git surface only — an
            # unambiguous prefix abbreviation. An inline `=value` is split off.
            flag_part, has_eq, inline_value = token.partition("=")
            canonical = alias_to_canonical.get(flag_part)
            if canonical is None and is_git_surface:
                prefix_matches = [
                    name for name in canonical_long_names if name.startswith(flag_part)
                ]
                # Exactly one match = unambiguous; >1 is ambiguous (git itself
                # rejects it, so the command never runs) and binds nothing.
                if len(prefix_matches) == 1:
                    canonical = prefix_matches[0]
            if canonical is None:
                i += 1
                continue
            if canonical in value_taking:
                if has_eq:                       # --repo=value
                    found.add(f"{canonical}={inline_value}")
                    i += 1
                elif i + 1 < n:                  # --repo value
                    found.add(f"{canonical}={tokens[i + 1]}")
                    i += 2
                else:                            # --repo (value missing; degenerate)
                    found.add(canonical)
                    i += 1
            else:
                # boolean: an explicit `=false`/`=0`/`=no` DISABLES the flag (the SAFE
                # form), so it must NOT bind — else an approval of `--admin=false`
                # would set-equal an execution of `--admin=true` (both → {--admin}) and
                # AUTHORIZE the escalation. Any other (or no) value binds.
                if not (has_eq and inline_value.lower() in _NEGATED_FLAG_VALUES):
                    found.add(canonical)
                i += 1
            continue

        # Short cluster (single dash): a general per-character walk that subsumes
        # the lone short (`-R`), the combined boolean cluster (`-sd`), the
        # attached short value (`-Rx`), and any mixed ordering (`-dR`, `-Rd`). A
        # value-taking short consumes the REST of the cluster (or the next token)
        # as its value and stops the walk — pflag semantics — so no bound short
        # is ever dropped from a cluster.
        cluster = token[1:]
        consumed_next = False
        j = 0
        while j < len(cluster):
            canonical = alias_to_canonical.get("-" + cluster[j])
            if canonical is None:
                j += 1
                continue
            if canonical in value_taking:
                remainder = cluster[j + 1:]
                if remainder.startswith("="):    # `-R=value`
                    remainder = remainder[1:]
                if remainder:                    # `-Rvalue`
                    found.add(f"{canonical}={remainder}")
                elif i + 1 < n:                  # `-R value`
                    found.add(f"{canonical}={tokens[i + 1]}")
                    consumed_next = True
                else:                            # `-R` (value missing; degenerate)
                    found.add(canonical)
                break
            found.add(canonical)                 # boolean short; keep walking
            j += 1
        i += 2 if consumed_next else 1

    return sorted(found)


def extract_command_context(command: str, flag_scan_text: str | None = None) -> dict:
    """Extract operation context FROM A COMMAND STRING (never prose).

    The shared SSOT both merge-guard hooks call. A key is PRESENT only when
    positively extracted; ABSENT otherwise (absence — NOT a None value — is the
    fail-closed signal). Possible keys:
        operation_type: "merge" | "close" | "force-push" | "branch-delete"
                        | "push-to-main" | "remote-ref-delete" | "remote-mass-delete"
                        | "branch-protection"
        pr_number:  str  (merge / close)
        branch:     str  (branch-delete — SINGLE target, exactly 1 positional)
        branch_set: str  (branch-delete — MULTI target #1129, >=2 positionals) —
                     canonical sort+dedup+quote-strip names via the shared netstring _canonical_join (`len:name` framing,
                     injective by construction, content-agnostic — no delimiter collision)
        target_ref: str  (force-push / push-to-main, KD-6; remote-ref-delete #1062a)
        mass_target: str (remote-mass-delete #1062b) — normalized identity STRING
                     _canonical_join([<sorted-mass-flags>, <remote-or-implicit-marker>, *<sorted-deduped-refspecs>])
        protected_branch: str (branch-protection #1063) — the branch from the
                     branches/<b>/protection URL path
        bound_flags: list[str]  (#1042) — sorted normalized privileged flags;
                     ALWAYS present when operation_type is (empty list when none).

    `flag_scan_text` (#1042) widens ONLY the privileged-flag scan to a fuller
    surface than `command` — the mint arm passes the full selected-option text so
    a flag positioned after a quoted argument is not lost to region truncation
    (the read arm passes nothing, scanning the raw command). Op/target are ALWAYS
    derived from `command` (region-anchored — preserves the anti-distractor
    multiplicity gate); only the flag scan honors `flag_scan_text`.
    """
    context: dict = {}
    # Line-continuation parity (mint==read by construction): join bash `\<newline>` on
    # BOTH the op/target-anchoring `command` AND the wider `flag_scan_text` BEFORE
    # detection, so the #1042 flag bind and the op/target derivation are continuation-
    # INVARIANT and identical on both arms. Without this, a faithful
    # `gh pr close 5 \<newline>--delete-branch` (whose region locate_command_regions now
    # joins upstream) still bound NO flag on the MINT arm — its flag scan reads the raw
    # option text whose `\<newline>` split `--delete-branch` off — while the READ arm,
    # scanning the clean executed command, bound {--delete-branch}; the set-equality
    # bind then REFUSED the faithful click (an over-block). Idempotent on already-
    # normalized input, a strict no-op without a literal `\<newline>`, and join-only —
    # it can only COMPLETE a split flag, never drop one.
    command = _normalize_line_continuations(command)
    if flag_scan_text is not None:
        flag_scan_text = _normalize_line_continuations(flag_scan_text)
    op_type = detect_command_operation_type(command)
    if op_type is None:
        return context
    context["operation_type"] = op_type
    # bound_flags is computed HERE (the single call site) so both arms inherit it
    # un-driftably. It is an ATTRIBUTE of the (op,target) pair, never part of pair
    # identity (_target_value / _collect_pairs ignore it), so flag variation can
    # never inflate the distinct-pair count and trip the multiplicity refusal.
    context["bound_flags"] = extract_privileged_flags(
        flag_scan_text if flag_scan_text is not None else command, op_type
    )
    if op_type in ("merge", "close"):
        pr_number = _extract_pr_number(command)
        if pr_number is None and op_type == "merge":
            pr_number = _extract_api_merge_pr(command)   # #1096 API pulls/<N>/merge
        if pr_number is not None:
            context["pr_number"] = pr_number
    elif op_type == "branch-delete":
        # Single-branch scalar path (BYTE-IDENTICAL): exactly ONE positional.
        branch = _extract_branch_name(command)
        if branch is not None:
            context["branch"] = branch
        else:
            # Multi-branch (>=2 positionals) FORCE-delete: bind the canonical
            # sorted+deduped branch-SET identity (#1129 R1, mirrors mass_target).
            # _extract_branch_name returns non-None ONLY for exactly 1 positional
            # and _extract_branch_delete_set ONLY for >=2, so they are MUTUALLY
            # EXCLUSIVE -> a command populates EXACTLY ONE of `branch` /
            # `branch_set` (the boundary discriminator). The distinct key plus the
            # op-type-identity check in the read switch keep a scalar token from
            # cross-authorizing a set command (and vice-versa).
            branch_set = _extract_branch_delete_set(command)
            if branch_set is not None:
                context["branch_set"] = branch_set
    elif op_type in ("force-push", "push-to-main"):
        # push-to-main reuses the force-push target parser: its target IS the
        # main/master ref that parser already returns for a plain push.
        target_ref = _extract_force_push_target_ref(command)
        if target_ref is not None:
            context["target_ref"] = target_ref
    elif op_type == "remote-ref-delete":
        # #1062a: REUSE the `target_ref` key — the parser yields a ref, the key is
        # semantically right, and the op-class identity (checked FIRST in the read
        # switch) keeps a remote-ref-delete token from cross-authorizing a
        # force-push/push-to-main with the same target_ref. Recognition⟺extractability:
        # detect returned this op IFF the extractor yields a ref, so it is non-None here.
        ref = _extract_remote_ref_delete_target(command)
        if ref is not None:
            context["target_ref"] = ref
    elif op_type == "remote-mass-delete":
        # #1062b: DISTINCT key `mass_target` (not target_ref — the value is a
        # normalized identity tuple, a different shape from a ref). op-identity-first
        # in the read switch already prevents cross-op match; a distinct key avoids any
        # accidental equality with a ref-shaped target_ref. Recognition⟺extractability.
        mass_target = _extract_mass_delete_target(command)
        if mass_target is not None:
            context["mass_target"] = mass_target
    elif op_type == "branch-protection":
        # #1063: the protected branch is PATH-resident (branches/<b>/protection).
        # Distinct key `protected_branch`; op-identity-first in the read switch keeps
        # it from cross-authorizing a branch-delete of the same branch name.
        protected_branch = _extract_protection_branch(command)
        if protected_branch is not None:
            context["protected_branch"] = protected_branch
    return context


# Shell compound + FD-redirect regexes — the SSOT for BOTH the read side
# (merge_guard_pre.is_compound_destructive_command re-imports these) AND the mint
# side (which runs is_compound_destructive_command on each locate_command_regions
# region). Centralized here so both sides scan on IDENTICAL separators (the #720
# anti-drift class).
#
# `_COMPOUND_OPS_RE` matches the COMPLETE bash command-separator/backgrounding set
# (P3): `&&`, `||`, `|&`, `;`, a bare `&` (background — the finding-#1 ride-along), a
# bare `|` shell pipe, and newline. Multi-char ops precede their single-char prefixes
# in the alternation so a match never mis-segments (`&&` before `&`; `||`/`|&` before
# `|`). Scanned on the P2 QUOTE-MASKED view so an operator inside a quoted argument is
# NOT a separator. FD-redirect / and-redirect / clobber tokens (`2>&1`, `1>&2`, `3<&0`,
# `&>`, `&>>`, `>|`) are NEUTRALIZED by `_FD_REDIRECT_RE` BEFORE the scan so the new
# bare-`&` arm does NOT false-positive on the bash redirect-both operator
# (`gh pr merge 5 &>out.log` is NOT a compound) — NOT via lookaround on the bare-pipe
# arm (an earlier lookbehind `(?<![0-9>])\|(?![<&])` had a spaceless-adjacency bypass:
# `... 2>&1|gh pr merge 999` slipped past; the structural pre-strip eliminates that
# class). `_FD_REDIRECT_RE`: `\d*[<>]&` (any `[<>]&` redirect prefix — fd-dup `2>&1`,
# fd-close `0<&-`, csh `>&out.log`) | `>\|` (clobber) | `&>>?` (and-redirect `&>`/`&>>`;
# the leading-`&` form). Audit: loosening
# `_COMPOUND_OPS_RE` must preserve the seven shapes; the `&>`/`&>>` neutralization must
# stay coupled to the bare-`&` arm; the pre-strip is the single source of truth.
_COMPOUND_OPS_RE = re.compile(r"&&|\|\||\|&|;|&|\||\n")
# `\d*[<>]&` neutralizes EVERY `[<>]&` redirect prefix — fd-dup (`2>&1`,`1>&2`),
# fd-close (`0<&-`,`1>&-`), and csh and-redirect-to-file (`>&out.log`) — so the bare-`&`
# arm cannot FP on any of them. A REAL background `&` is whitespace-preceded (` & `),
# never `[<>]`-preceded, so it still detects. `&>>?` covers the leading-`&` and-redirect
# (`&>`/`&>>`); `>\|` the clobber.
_FD_REDIRECT_RE = re.compile(r"\d*[<>]&|>\||&>>?")



def locate_command_regions(text: str) -> list[str]:
    """Return ALL gh/git destructive-command regions in ONE string, in document
    order.

    A region is a candidate command substring — a quoted region (via
    `_QUOTED_COMMAND_RE`) OR a bare `gh ...`/`git ...` span (via
    `_BARE_COMMAND_RE`) — that `detect_command_operation_type` classifies
    non-None.

    Takes a SINGLE string, NEVER an options array (D3 structural invariant: the
    function can never receive non-selected options, so it CANNOT over-scan —
    'make illegal states unrepresentable' on a security boundary). The caller
    passes ONE question's text or ONE selected option's text at a time.
    """
    # Mint==read parity: join bash line-continuations (`\<newline>` -> space) BEFORE
    # the region scans, so the mint joins continuations IDENTICALLY to the read side
    # (is_dangerous_command + is_compound_destructive_command both normalize first).
    # Without this, `gh pr close 5 \<newline>--delete-branch` truncated at the newline
    # to a non-dangerous region (`gh pr close 5 \`) -> mint withheld a token while the
    # read side (normalized) DENIED the full command -> a faithful click was OVER-blocked.
    # Offset/length note: the join SHORTENS text (2 chars -> 1), but every offset below
    # (the `covered` quoted spans, the masked-view bare-span slices) indexes into THIS
    # SAME normalized `text`, and the function returns region STRINGS (never offsets into
    # the caller's original), so the length change is internally consistent and invisible
    # to every caller. Join-only + no-op without a literal `\<newline>` -> detection can
    # only INCREASE (never drop), so no new under-block.
    text = _normalize_line_continuations(text)
    regions: list[str] = []
    covered: list[tuple[int, int]] = []
    # Quoted regions first — an explicit command literal is the canonical form.
    # COVER only quoted regions that ARE commands: a non-command quoted ARGUMENT
    # (`--comment "x"`) must NOT be covered, else the masked-view bare span below
    # (which now extends THROUGH it) would be wrongly skipped and drop #5's trailing
    # flag. An embedded quoted COMMAND, by contrast, IS covered + captured separately
    # so the multiplicity gate still refuses a distractor.
    for match in _QUOTED_COMMAND_RE.finditer(text):
        candidate = match.group(1) or match.group(2) or match.group(3)
        if candidate and detect_command_operation_type(candidate) is not None:
            covered.append((match.start(), match.end()))
            regions.append(candidate)
    # Bare gh/git spans located on the P2 QUOTE-MASKED view so a quoted ARGUMENT in
    # the MIDDLE of a command (`--comment "x"`) no longer truncates the span and
    # drops a trailing flag (#5). Single/double-quoted spans mask to spaces (the bare
    # span extends through them); real separators (`;` `|` `&` newline) and backticks
    # are NOT masked, so they still bound the span. Region text is sliced from the
    # ORIGINAL so the real quoted value is preserved. The skip is CONTAINMENT (the
    # bare span lies ENTIRELY within an already-captured quoted command, e.g. a
    # backtick command) — NOT mere overlap: the outer command of a distractor
    # `... "gh pr merge 999"` CONTAINS the covered inner region rather than being
    # contained by it, so it is still added → two regions → multiplicity refuses.
    masked = _mask_shell_quotes(text)
    for match in _BARE_COMMAND_RE.finditer(masked):
        if any(
            c_start <= match.start() and match.end() <= c_end
            for c_start, c_end in covered
        ):
            continue
        span = text[match.start():match.end()].strip()
        if detect_command_operation_type(span) is not None:
            regions.append(span)
    return regions


def locate_command_region(text: str) -> str | None:
    """Convenience: the first command region in `text`, else None. SINGLE
    string arg (same D3 invariant as locate_command_regions)."""
    regions = locate_command_regions(text)
    return regions[0] if regions else None


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
    leaving a token to expire silently. TOKEN_TTL already expires
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


# ---------------------------------------------------------------------------
# Read-floor danger predicates (GAP1/GAP5) — PROMOTED from merge_guard_pre.py so
# BOTH the read hook AND the mint hook (merge_guard_post) call the SAME predicate:
# the mint gates its token-write on is_dangerous_command (mint⊆read by construction)
# and refuses any compound via is_compound_destructive_command. pre.py re-imports
# these. _COMPOUND_OPS_RE/_FD_REDIRECT_RE already live above (GAP5 elevation).
# ---------------------------------------------------------------------------

DANGEROUS_PATTERNS = [
# PR merge via gh CLI
re.compile(_GH_PREFIX + r"pr\s+merge\b"),
# PR close with --delete-branch: the close+--delete-branch danger arms live in
# _CLOSE_LITERAL_ARMS (defined with the classifier patterns above) — matched
# PER-LEG by is_dangerous_command after this list misses (#1087 leg isolation),
# NOT whole-string here (a whole-command match fired cross-leg and laundered an
# ambiguous multi-close into an escalated-single token). Bare close is reversible.
# Force push arms live in _FORCE_PUSH_LITERAL_ARMS (defined with the classifier
# patterns above) — matched PER-LEG by is_dangerous_command after this list
# misses (#1082 leg isolation), NOT whole-string here.
# Force-branch-delete arms live in _BRANCH_DELETE_LITERAL_ARMS (defined with the
# classifier patterns above) — matched PER-LEG by is_dangerous_command after this
# list misses (#1094 leg isolation), NOT whole-string here (a whole-command match
# fired cross-leg on a stray -D / --delete --force token in a benign leg).
# API danger arms (merge / git-refs / branch-protection / contents / implicit-POST,
# across gh api / curl / wget) live in _API_LITERAL_ARMS (defined with the classifier
# patterns above) — matched PER-LEG by is_dangerous_command after this list misses
# (#1086 leg isolation), NOT whole-string here (a whole-command match fired cross-leg
# and over-blocked a benign compound carrying a method/body-flag token in a benign leg).
# Direct push to default branch (bypasses PR merge)
re.compile(_GIT_PREFIX + r"push\s+\S+\s+HEAD:main\b"),
re.compile(_GIT_PREFIX + r"push\s+\S+\s+HEAD:master\b"),
# Regular push to main/master (e.g., local merge then push)
# Negative lookahead (?!:) prevents matching refspecs like main:feature-branch.
# The dash-flag walk is BOUNDED {0,K} — defense-in-depth that removes the last
# unbounded `*` prefix walk in the push patterns so their linearity is
# structural/intrinsic rather than contingent on the global-flag prefix bound
# (#1001 family); already linear at HEAD, not a hang-fix.
re.compile(_GIT_PREFIX + r"push\s+(?:-\S+\s+){0,%d}\S+\s+main(?!:)\b"   % _MAX_GLOBAL_FLAG_TOKENS),
re.compile(_GIT_PREFIX + r"push\s+(?:-\S+\s+){0,%d}\S+\s+master(?!:)\b" % _MAX_GLOBAL_FLAG_TOKENS),
]


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


# Path-qualified shell token. Trailing (?![\w/]) anchors the shell name as a
# whole PATH-LEAF token: excludes prefix-of-name (`>(basht)`/`>(teehee)`) AND
# `>(bash/foo)` (bash is a DIRECTORY, foo the executable) while KEEPING
# metachar-separated real vectors `>(bash;ls)`/`>(bash&&x)`/`>(bash|cat)`
# (bash still executes).
#
# ReDoS — ReDoS-free AS USED, NOT standalone. Both arms anchor this token behind
# `>\(`, so re.search only attempts it at the handful of `>(` offsets in a real
# command. STANDALONE the nested `(?:[^\s)/]*/)*` is O(N^2): re.search retries at
# EVERY start position (multi-offset retry) with an O(N) per-offset forward scan
# — measured ~4x per input-doubling on pathological no-slash / all-slash input.
# This is NOT within-match catastrophic backtracking (an anchored re.match is
# linear, ~2x/double), so an atomic group `(?>...)` would NOT fix it (and atomic
# groups are unavailable anyway — requires-python >=3.7). DO NOT reuse this token
# UNGATED; if it is ever needed ungated, bound the path segments
# `(?:[^\s)/]*/){0,K}` (the F1 mechanism), which caps the per-offset scan.
_PROCSUB_SHELL = r"(?:[^\s)/]*/)*(?:bash|sh|zsh)(?![\w/])"


def _has_process_substitution_to_shell(command: str) -> bool:
    """Check if a command uses process substitution fed to a shell interpreter.

    Detects:
      - input-side  ``bash <(echo "...")``  — the shell consumes the substitution
        as its input script (the original guard, UNCHANGED);
      - output-side ``echo "..." > >(bash)`` — the command's stdout is routed into
        a shell via process substitution. Caught in two forms (#1002):
          * Arm A — ``>(shell)`` as a stdout-routing REDIRECT TARGET. The operator
            set is stdout-only (``>``, ``>>``, ``1>``, ``1>>``, ``&>``, ``&>>``,
            the csh ``>&`` excluding the fd-duplication ``>&N``, and the clobber
            ``>|``); stderr-only routing (``2>``/``3>``) is excluded by omission.
          * Arm B — ``>(shell)`` as a command ARGUMENT (tee-fanout & general, e.g.
            ``... | tee >(bash)``). Keyed on a preceding NON-redirect token (word
            char, quote, or close-bracket), so ``2> >(bash)`` (preceded by ``>``)
            is NOT matched — the stderr exclusion holds on this arm too.
    Both output-side arms accept an optional path prefix (``>(/bin/bash)``,
    ``>(./sh)``) and require the shell name as a whole path-leaf token: non-shell
    targets (``> >(tee ...)``, ``> >(cat ...)``), prefix-of-name (``>(teehee)``,
    ``>(basht)``), and ``>(bash/foo)`` (bash a directory) are NOT matched.

    The guard is consumed ONLY as a strip-SKIP condition: a True result PRESERVES
    content for the dangerous-pattern scan, so widening it is monotonically
    detection-increasing (INV-D2-safe; cannot create a false-negative).
    """
    return bool(
        re.search(r"\b(?:bash|sh|zsh)\s+<\(", command)                       # input-side (unchanged)
        # Arm A — redirect TARGET, stdout-routing operators only (stderr excluded by construction):
        or re.search(r"(?:&>>?|>&(?![0-9])|>\||1>>?|(?<![0-9])>>?)\s*>\(\s*" + _PROCSUB_SHELL, command)
        # Arm B — procsub as a command ARGUMENT (tee-fanout & general): preceded by a NON-redirect token:
        or re.search(r"[\w\"')\]}]\s+>\(\s*" + _PROCSUB_SHELL, command)
    )


# Heredoc-BODY excision for the routing-flag view (#1129 R3). Reuses carrier-1's
# marker grammar but replaces the BODY ONLY: the opener line (including a
# genuinely-executing `| bash` / `> >(bash)` TAIL) and the closing marker stay
# visible to the routing scan. Carrier-1's whole-match replacement would swallow
# the opener tail and regress the pinned heredoc-pipe/procsub canaries
# (under-block). Same input-side guard as carrier 1: a shell-fed body
# (`bash <<EOF`) executes, so it is preserved. The REAL carrier-1 strip is
# intentionally left untouched (this excision builds a view-only scan surface).
_HEREDOC_BODY_RE = re.compile(
    r"(<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n)"   # 1: operator+marker+opener TAIL (kept)
    r"(?:.*?\n)?"                            #    body (excised)
    r"(\t*\2(?![\w]))",                      # 3: closing marker (kept)
    re.DOTALL,
)


def _excise_heredoc_bodies_for_routing_scan(command: str) -> str:
    if "<<" not in command:          # cheap short-circuit (perf: common case)
        return command

    def _repl(m: "re.Match") -> str:
        preceding = command[: m.start()].rstrip()
        if re.search(r"\b(?:bash|sh|zsh)\s*$", preceding):
            return m.group(0)        # shell-fed heredoc: body executes — keep
        return m.group(1) + "HEREDOC_BODY_EXCISED\n" + m.group(3)

    return _HEREDOC_BODY_RE.sub(_repl, command)


def _excise_and_mask(command: str) -> tuple[str, str]:
    """Shared prefix for the two routing-flag views (#1129 R3): excise heredoc
    bodies (opener line + closing marker kept; shell-fed bodies preserved), then
    space-mask every balanced quoted span via _mask_shell_quotes. Returns
    (excised, view). ORDER IS LOAD-BEARING: excision FIRST removes stray body
    quotes that could desync the quote mask. _mask_shell_quotes is SAME-LENGTH,
    so view and excised align 1:1 by offset."""
    excised = _excise_heredoc_bodies_for_routing_scan(command)
    view = _mask_shell_quotes(excised)
    return excised, view


def _executed_surface_view(command: str) -> str:
    """Routing-flag scan surface (#1129 R3): the command with non-executed
    data removed — heredoc BODIES excised (opener line + closing marker kept;
    shell-fed bodies preserved), then every balanced quoted span masked to
    spaces via _mask_shell_quotes (defined later in this module; resolved at
    call time). Fail direction: unbalanced/ambiguous quoting leaves text
    UNMASKED -> the routing token stays visible -> detection preserved (at
    worst a residual over-block on malformed input, never an under-block).
    ORDER IS LOAD-BEARING: heredoc excision FIRST removes stray body quotes
    that could desync the quote mask. Consumed ONLY by the hoisted
    piped_to_shell / process_sub_to_shell computation; never fed to
    DANGEROUS_PATTERNS and never returned as strip output."""
    return _excise_and_mask(command)[1]


def _procsub_anchor_view(command: str) -> str:
    """OUTPUT-SIDE process-substitution routing view (#1129 R3-fix). The space-mask
    executed-surface view, then arm B's preceding-token ANCHOR restored ONLY
    immediately-left of a SURVIVING >(shell) whose writer token was a masked quoted
    span. Everything from >( rightward is viewed EXACTLY as the space-mask (so the
    incidental >("ba"sh)->>(    sh) catch is preserved). Re-catches the R3 arm-B
    anchor regression WITHOUT a blanket fill (which breaks the shell-name region ->
    a NEW under-block on >("ba"sh)) and WITHOUT computing arm B over raw (which
    would re-flag a `>(shell)` sitting inside quoted carrier data). Relies on
    _mask_shell_quotes being SAME-LENGTH: view and excised align 1:1 by offset.
    Consumed ONLY by process_sub_to_shell."""
    excised, view = _excise_and_mask(command)        # excise + space-mask; offsets aligned
    if ">(" not in view:                             # cheap short-circuit (common case)
        return view
    out = list(view)
    for m in re.finditer(r">\(", view):              # each SURVIVING >( on the view
        i = m.start()
        k = i - 1
        # skip a genuine UNMASKED bash blank (space OR tab): view==excised at an unmasked position
        while k >= 0 and view[k] == excised[k] and excised[k] in " \t":
            k -= 1
        # stop char masked to space in the view but a CLOSING QUOTE in raw ==
        # a quoted writer token ended here -> reveal its closing quote (in arm B's
        # class ['\w"')\]}]) so `<anchor>\s+>\(` matches. A redirect op (>, 2>) or a
        # genuine separator is NOT a quote -> no restore -> arm A / stderr-exclusion
        # and the absent-anchor case are all untouched.
        if k >= 0 and view[k] == " " and excised[k] in "\"'":
            out[k] = excised[k]
    return "".join(out)


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


# Span-scoped command-substitution preserve (#1140). The two scanners below let
# _keep_carrier_value preserve ONLY the genuine `$(...)`/backtick SPANS of a value
# (they execute -> stay caught) while stripping the surrounding INERT literal — the
# cure for the over-block where a benign `$(date)` beside danger-looking prose caused
# the WHOLE value to be preserved. Both are single-pass char walks (O(n), no regex
# backtracking): each advances its cursor monotonically and visits every char O(1) times.
def _extract_dollar_paren(c, i):
    """Return the balanced ``$(...)`` span starting at c[i] (c[i]=='$', c[i+1]=='(')
    and the index just past it, via a quote-aware + escape-aware depth counter: a ``)``
    inside a NESTED ``$(...)`` or inside a quoted span does NOT close the outer span.
    Returns (None, len(c)) when the span is unterminated."""
    n = len(c); depth = 0; j = i
    while j < n:
        ch = c[j]
        if ch == "\\": j += 2; continue
        if ch in "\"'":
            q = ch; j += 1
            while j < n:
                if c[j] == "\\" and q == '"': j += 2; continue
                if c[j] == q: j += 1; break
                j += 1
            continue
        if ch == "(": depth += 1; j += 1; continue
        if ch == ")":
            depth -= 1; j += 1
            if depth == 0: return c[i:j], j
            continue
        j += 1
    return None, n   # unterminated


def _extract_backtick(c, i):
    """Return the balanced backtick span starting at c[i] (c[i]=='`') and the index
    just past it. Returns (None, len(c)) when the span is unterminated."""
    n = len(c); j = i + 1
    while j < n:
        if c[j] == "\\": j += 2; continue
        if c[j] == "`": return c[i:j+1], j+1
        j += 1
    return None, n


def _preserve_substitution_spans(value):
    """Replace literal (inert) text with 'STRIPPED'; preserve $(...)/`...` spans VERBATIM.
    Escaped \\$( and \\` are inert. value is a dq "..." (arm 1) or an unquoted VALUE-TOKEN (arm 3).
    QUOTE-CONTEXT-FAITHFUL: inside a dq value a ' is a LITERAL apostrophe (bash does not treat
    it as structural), so dq-inner folds it into the stripped literal run (else ubiquitous
    it's/don't over-block). A " in dq-inner cannot occur well-formed (arm 1 matched
    "(?:[^"\\]|\\.)*") -> malformed -> fail-safe. In the UNQUOTED arm BOTH ' and " stay
    structural -> fail-safe. Return None -> FAIL-SAFE (caller preserves whole value = today's
    behavior) for: unterminated span, a preserved span carrying a quote, a " in dq-inner, or
    either quote in the unquoted arm."""
    def _scan(c, dq_inner):
        out = []; lit = False; i = 0; n = len(c)
        def flush():
            nonlocal lit
            if lit: out.append("STRIPPED"); lit = False
        while i < n:
            ch = c[i]
            if ch == "\\": lit = True; i += 2 if i+1 < n else 1; continue
            if ch == "$" and i+1 < n and c[i+1] == "(":
                span, j = _extract_dollar_paren(c, i)
                if span is None or '"' in span or "'" in span: return None
                flush(); out.append(span); i = j; continue
            if ch == "`":
                span, j = _extract_backtick(c, i)
                if span is None or '"' in span or "'" in span: return None
                flush(); out.append(span); i = j; continue
            if ch == '"': return None              # structural dq terminator -> fail-safe (malformed in dq-inner)
            if ch == "'" and not dq_inner: return None   # unquoted: ' is structural -> fail-safe
            lit = True; i += 1                     # dq-inner ' falls through -> LITERAL
        flush()
        return "".join(out)
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        inner = _scan(value[1:-1], True)
        return None if inner is None else '"' + inner + '"'
    return _scan(value, False)                     # unquoted VALUE-TOKEN


def _keep_carrier_value(m: "re.Match") -> str:
    """Shared value replacer for the gh/git prose-carrier strips (#1129 R2): issue/pr
    create|edit|comment, release create|edit, gist create|edit, git commit/tag -m, and
    (new) merge/stash/notes. Passed as `keep_fn` to _strip_flag_values (arms 1 & 3 —
    double-quoted + unquoted VALUE-TOKEN). Inert prose (no $()/backtick) -> the
    flag(+separator) is kept and the value replaced with the inert `STRIPPED` bareword.
    A value CONTAINING command substitution is SPAN-SCOPED (#1140): only the genuine
    `$(...)`/backtick spans are preserved VERBATIM (they execute -> stay caught) while
    the surrounding inert literal is stripped, so a benign `$(date)` beside
    danger-looking prose no longer preserves the whole value (the over-block cure). On a
    span the scanner cannot safely resolve (unterminated span, a preserved span carrying
    a quote, or a structural quote in the value) it FAILS SAFE — the whole value is
    preserved (today's behavior), never dropped. The single-quoted arm strips
    unconditionally inside _strip_flag_values (untouched)."""
    if not _has_command_substitution(m.group(0)):
        return m.group(1) + "STRIPPED"          # inert prose -> strip (unchanged)
    flag = m.group(1); value = m.group(0)[len(flag):]
    transformed = _preserve_substitution_spans(value)
    if transformed is None:
        return m.group(0)                       # FAIL-SAFE: preserve whole value
    return flag + transformed


def _strip_flag_values(span: str, flag_sep_regex: str, keep_fn) -> str:
    """Quote-safe value strip for a flag family within a single command span (#1118
    re-model). KEEPS the flag(+separator) token; replaces the VALUE with a BALANCED
    placeholder. Three arms by value-quoting (order load-bearing: quoted arms first, so
    the unquoted VALUE-TOKEN arm never re-touches an already-stripped quoted value):
      1. double-quoted value  -> keep_fn (preserves $()/backtick cmd-sub — it executes)
      2. single-quoted value  -> unconditional 'STRIPPED' (single quotes never expand)
      3. unquoted VALUE TOKEN  -> keep_fn (Q-SAFE: consumes a COMPLETE quote-balanced token
                                  incl. embedded quoted spans, so it can NEVER emit a
                                  dangling quote that would merge legs — the SEC-1/SEC-2
                                  root-cause fix).
    `flag_sep_regex` MUST be ONE capturing group (keep_fn uses m.group(1); the sq arm
    backrefs `\\1`) and must introduce no OTHER capturing group. SHARED by carriers 8 & 9
    so the two value strips can never drift again (the shared non-quote-aware matcher was
    the #1118 root cause).
    """
    span = re.sub(flag_sep_regex + r'"(?:[^"\\]|\\.)*"', keep_fn, span)     # 1
    span = re.sub(flag_sep_regex + r"'[^']*'", r"\1'STRIPPED'", span)       # 2
    span = re.sub(flag_sep_regex + _VALUE_TOKEN, keep_fn, span)             # 3
    return span


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

    # Output-side execution-routing flags — computed ONCE for ALL stdout-
    # producing content carriers (heredoc/echo/commit-msg/here-string/gh-
    # creation). When the command pipes its output to a shell or feeds a shell
    # via OUTPUT-side process substitution (`> >(bash)`), a stripped dangerous
    # literal would still EXECUTE downstream, so those carriers must SKIP
    # stripping (preserve content → detect). Hoisted above carrier 1 so the
    # heredoc carrier can consult them too. MONOTONIC: a True flag only ADDS
    # detection (skip strip → more content scanned); never removes it.
    #
    # The flags defend the EXECUTED surface, so they are computed over the
    # executed-surface view (#1129 R3): quoted spans masked, heredoc bodies
    # excised. Carrier DATA (a `| sh` or `>(bash)` inside a quoted value or
    # heredoc body) can no longer disable the carriers; genuinely-executing
    # routing (unquoted tails, opener-line tails) is unquoted shell structure
    # and survives the view at identical offsets.
    #
    # The two flags read DIFFERENT views (#1129 R3-fix): piped_to_shell stays on
    # the pure space-mask executed-surface view (the security 216-case pipe sweep
    # stays byte-valid). process_sub_to_shell reads _procsub_anchor_view — the same
    # space-mask with arm B's LEFT anchor restored immediately-left of a surviving
    # >(shell) whose writer was a masked quoted token — re-catching the R3 arm-B
    # regression (`echo "…" | "tee" >(bash)`) without a blanket fill (which would
    # regress `>("ba"sh)`) and without computing arm B over raw (which would re-flag
    # a `>(shell)` sitting inside quoted carrier data).
    piped_to_shell = _has_pipe_to_shell(_executed_surface_view(command))
    process_sub_to_shell = _has_process_substitution_to_shell(_procsub_anchor_view(command))

    # 1. Strip heredoc bodies: << 'EOF' ... EOF, << EOF ... EOF, << "EOF" ... EOF
    #    Match the heredoc marker, then everything up to and including the
    #    closing marker on its own line.
    #    GUARD (input-side): the inner check preserves the body if the heredoc
    #    is fed to a shell interpreter (e.g. bash << EOF ... EOF — body executes).
    #    GUARD (output-side): the outer piped/process-sub skip preserves the body
    #    when it is routed to a shell via `| bash` / `> >(bash)`. The two COMPOSE.
    if not piped_to_shell and not process_sub_to_shell:
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
    #    (piped_to_shell / process_sub_to_shell are hoisted to the top.)
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

    # 5. git commit MESSAGE carrier — SPAN-BOUNDED + FLAG-ANCHORED (migrated from the
    #    former inline -m-only literal-quote arms to the shared quote-balanced machinery;
    #    a structural clone of carrier-7d `git tag -m`). The -m/--message argument is a
    #    commit message, never executed directly. FLAG-anchored to the -m/--message VALUE
    #    only; BOUNDED to a git-commit span whose body (the SAME quote-aware body as
    #    carriers 7/7b/7c/7d) STOPS at the first UNQUOTED ;/&&/|/newline so an executing
    #    tail stays OUTSIDE the span and is caught. The prefix is a NON-gobbling
    #    `git <bounded non-separator words> commit` (handles global flags like -C <path>)
    #    whose word class EXCLUDES ;&| so it CANNOT cross an unquoted separator —
    #    deliberately NOT _GIT_PREFIX, whose (?:\S+\s+){0,N} gobbler spans separators and
    #    would let a later `git commit` pull an intermediate `;gh …;` into ONE span,
    #    re-opening the #1129-class leg-merge under-block. Bounded {0,N} keeps it linear.
    #    GUARD (output-side): a commit SUBJECT is echoed to git's stdout, so
    #    `git commit -m "..." > >(bash)` (or `| bash`) routes it to a shell — the outer
    #    piped/process-sub skip preserves it for detection (#1002).
    #    GUARD (cmd-subst): $()/backtick in a double-quoted value preserves — rides on
    #    _keep_carrier_value.
    if not piped_to_shell and not process_sub_to_shell:
        _git_commit_span = (
            r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}commit\b" % _MAX_GLOBAL_FLAG_TOKENS
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _git_commit_span,
            lambda mm: _strip_flag_values(
                mm.group(0),
                # Long arm accepts any unambiguous abbreviation of --message
                # (--m -> --message): on `git commit` the ONLY --m* option IS
                # --message, so a --m-prefix can never over-strip another option's
                # value. Short arm (-m/bundled -am/attached -mMSG) unchanged.
                r"((?:--m(?:e(?:s(?:s(?:a(?:g(?:e)?)?)?)?)?)?|-[a-ln-zA-Z]*m)\s*)",
                _keep_carrier_value,
            ),
            result,
        )

    # 6. Strip here-string quoted arguments: <<< "..." or <<< '...'
    #    Here-strings pass text as stdin, not as a command.
    #    GUARD (input-side): the inner check preserves content if a shell
    #    interpreter precedes the <<< (e.g. bash <<< "dangerous" — executes).
    #    GUARD (cmd-subst): preserve double-quoted content containing $()/backtick.
    #    GUARD (output-side): the outer piped/process-sub skip preserves content
    #    routed to a shell via `| bash` / `> >(bash)`. The guards COMPOSE.
    if not piped_to_shell and not process_sub_to_shell:
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

    # 7. Strip gh issue/pr CREATION/COMMENT-carrier quoted arguments.
    #    `gh issue create/edit/comment` and `gh pr create/comment` accept
    #    --title/--body (and the -t/-b aliases) whose VALUE is prose sent to the
    #    GitHub API — never executed by a shell. A dangerous-op literal named inside
    #    that prose (e.g. `gh issue create --title "...git branch -D x..."`)
    #    must not trip DANGEROUS_PATTERNS. Strip the quoted value; keep the
    #    verb + flag tokens visible.
    #
    #    SCOPE (INV-D2) — exempts ONLY the non-executing ARGUMENT text of a
    #    CREATION carrier. Does NOT match `gh pr close` (a real close-class
    #    destructive verb; `--delete-branch` is the deny trigger) — `close`
    #    is absent from the verb alternation by construction, so a
    #    `gh pr close ... --delete-branch` command is NOT stripped and
    #    DANGEROUS_PATTERNS still fires.
    #
    #    GUARD: same indirection guards as the echo/printf carrier — the
    #    outer `piped_to_shell` / `process_sub_to_shell` skip (set at step 3)
    #    covers pipe-to-shell / process-sub-to-shell; the double-quoted arm
    #    additionally preserves a value containing command substitution
    #    `$(`/backtick (it would execute). Single-quoted values never expand,
    #    so they need only the outer skip (mirroring carriers 3 and 5).
    #    `--body-file`/`-F` is NOT a carrier: it names a FILE whose content
    #    is not on the command line, so there is nothing on the line to strip.
    if not piped_to_shell and not process_sub_to_shell:
        # Match the carrier COMMAND span first (verb + its arguments), then
        # strip EVERY --title/--body/-t/-b value within that span. A single
        # re.sub on the whole command would strip only the FIRST flag-value
        # (the verb prefix is consumed by the first match and cannot re-anchor
        # on a bare second flag), so the per-span inner-strip is required to
        # strip both a `--title` and a `--body` on one command.
        #
        # The span body is QUOTE-AWARE: it consumes balanced quoted regions
        # atomically (so `;`/`&`/`|`/newline INSIDE a quoted value are not
        # separators) and stops at the first UNQUOTED `&`/`|`/`;`/newline; an
        # unbalanced quote stops the span early (under-consume = over-block,
        # never under-block). This is load-bearing for INV-D2: an unquoted
        # executing op always terminates the span (none of the three body
        # alternatives can begin at an unquoted separator), so a compound's
        # executing tail (e.g. `... && git branch -D real`) falls OUTSIDE the
        # span and is NEVER stripped — it stays caught. The three alternatives
        # have DISJOINT first chars (non-sep-non-quote / `"` / `'`), so the
        # nested `*` has no backtracking ambiguity (linear; no ReDoS). The
        # double-quoted alternative honors `\"` escapes, matching bash's
        # escaped-quote semantics so the regex cannot desync from the shell.
        # Verb alternation: issue create|edit|comment, pr create|comment. NOT pr
        # close — `close` is absent by construction so a close command never
        # matches. `comment` is a non-executing carrier exactly like create/edit:
        # its --body/-b value is API prose, and the SAME doubly-anchored strip
        # (carrier verb + value DIRECTLY after --body/-t/-b) + quote-aware span +
        # $()/backtick-preserve guard apply, so it inherits the create/edit
        # safety — empirically verified: escaped-quote/escaped-dq/metachar bodies
        # are handled correctly (op inside a dq/sq body is inert and stripped; an
        # op OUTSIDE the body, after an unquoted separator OR a bare escaped quote
        # not following a carrier flag, is NEVER stripped and stays caught).
        _gh_carrier_span = (
            r"gh\s+(?:issue\s+(?:create|edit|comment)|pr\s+(?:create|comment|edit))\b"
            + _VERB_MSG_BODY
        )

        def _strip_gh_carrier_span(span_match: re.Match) -> str:
            # Migrated (#1129 R2) to the SHARED quote-balanced _strip_flag_values (#1118
            # machinery, same as carriers 8/9): its 3 arms
            # (dq→keep_fn / sq→'STRIPPED' / unquoted VALUE-TOKEN→keep_fn) replace the
            # former inline dq+sq re.sub. The unquoted VALUE-TOKEN arm ADDS coverage of
            # ANSI-C `$'…'` + adjacent-concat `"a"'b'` body forms (consumed ATOMICALLY, so
            # no dangling quote → no leg-merge). $()/backtick preserve rides on
            # _keep_carrier_value. `edit` added to the pr alternation (OB1).
            return _strip_flag_values(
                span_match.group(0),
                r"((?:--title|--body|-t|-b)\s+)",
                _keep_carrier_value,
            )

        result = re.sub(_gh_carrier_span, _strip_gh_carrier_span, result)

        # 7b. gh release CREATE/EDIT carrier (#1129 R2). --notes/-n + --title/-t carry API
        #     prose (never executed). EXCLUDE --notes-file/-F (a FILE, off-line) AND the
        #     -d/--draft + -p/--prerelease BOOLEANS (arity verified vs gh 2.96.0) — a
        #     boolean must never enter a value-strip set or the strip mis-consumes the
        #     following token (PER-CARRIER flag sets, NEVER a union). Subcommand-specific
        #     (create|edit, NEVER bare `gh release`) so `gh release delete` can never
        #     start a carrier span. Shared quote-balanced _strip_flag_values + $()-preserve.
        _gh_release_span = (
            r"gh\s+release\s+(?:create|edit)\b"
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _gh_release_span,
            lambda m: _strip_flag_values(
                m.group(0), r"((?:--notes|-n|--title|-t)\s+)", _keep_carrier_value
            ),
            result,
        )

        # 7c. gh gist CREATE/EDIT carrier (#1129 R2). --desc/-d is API prose. `-d` is
        #     admissible ONLY here (gist -d=--desc VALUE; contrast release -d=--draft
        #     BOOLEAN — the reason PER-CARRIER flag sets are mandatory). Subcommand-specific
        #     (create|edit, NEVER bare `gh gist`) so `gh gist delete` never starts a carrier.
        _gh_gist_span = (
            r"gh\s+gist\s+(?:create|edit)\b"
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _gh_gist_span,
            lambda m: _strip_flag_values(
                m.group(0), r"((?:--desc|-d)\s+)", _keep_carrier_value
            ),
            result,
        )

        # 7d. git tag MESSAGE carrier (#1129 R2; F1 leg-merge fix, HALT #64) — SPAN-BOUNDED
        #     + FLAG-ANCHORED. `git tag -m/--message` (benign annotation prose) shares the
        #     `tag` verb with the DESTRUCTIVE `git tag -d/--delete`, so the strip is
        #     FLAG-anchored to the -m/--message VALUE only (never touches -d), and BOUNDED
        #     to a git-tag span whose body (the SAME quote-aware body as 7/7b/7c) STOPS at
        #     the first UNQUOTED ;/&&/|/newline. The span bound is LOAD-BEARING: the prior
        #     WHOLE-COMMAND strip let _strip_flag_values arm 3 (unquoted VALUE-TOKEN, which
        #     does NOT stop at ;&|) re-touch the arm-1 `STRIPPED` bareword and consume
        #     `STRIPPED;gh` together — EATING the next leg's `gh` head
        #     (`git tag -m "x";gh pr merge 5 --delete-branch` -> gh gone -> auto-ALLOW). The
        #     span stops at the separator, so the executing tail stays OUTSIDE and caught.
        #     The prefix is a NON-gobbling `git <bounded non-separator words> tag`
        #     (handles global flags like `-C <path>`) whose word class EXCLUDES `;&|` so it
        #     CANNOT cross an unquoted separator — deliberately NOT `_GIT_PREFIX`, whose
        #     `(?:\S+\s+){0,N}` gobbler (its `\S+` spans separators) could cross a `;gh …;`
        #     into a LATER `git tag`, re-opening the leg-merge on a command like
        #     `git commit -m "x";gh pr merge 5;git tag v1`. Bounded `{0,N}` (same
        #     _MAX_GLOBAL_FLAG_TOKENS as _GIT_PREFIX) keeps the match linear/sub-quadratic.
        #     `-d` stays visible (only the -m value strips, within the span); a faithful
        #     `git tag -m "…" v1` has no unquoted separator -> one span -> OB3 still strips.
        #     $()/backtick preserve rides on _keep_carrier_value.
        _git_tag_span = (
            r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}tag\b" % _MAX_GLOBAL_FLAG_TOKENS
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _git_tag_span,
            lambda mm: _strip_flag_values(
                mm.group(0),
                # Long arm is BOUNDED to --mes...--message — DELIBERATELY DIFFERENT
                # from the commit anchor. `git tag` ALSO has the value-taking
                # --merged/--no-merged options, which diverge from --message at char
                # 3 (r != s), so starting the arm at --mes can never consume their
                # values (--m/--me are ambiguous and git rejects them). Do NOT
                # re-unify with the commit anchor. Short arm unchanged.
                r"((?:--mes(?:s(?:a(?:g(?:e)?)?)?)?|-[a-ln-zA-Z]*m)\s*)",
                _keep_carrier_value,
            ),
            result,
        )

        # 7e. Sibling message-carrying git verbs — SPAN-BOUNDED + FLAG-ANCHORED, the same
        #     machinery as carriers 5/7d. `git merge`, `git stash push/store/save`, and
        #     `git notes add/append` accept a -m/--message (or, for `stash save`, a
        #     POSITIONAL) whose value is annotation prose, never executed. A destructive
        #     literal named inside that prose (e.g. `git merge -m "...git branch -D x..."`)
        #     must not trip DANGEROUS_PATTERNS. Each span is a NON-gobbling
        #     `git <bounded non-separator words> <verb>` (word class EXCLUDES ;&| so it
        #     CANNOT cross an unquoted separator — deliberately NOT _GIT_PREFIX, whose
        #     gobbler would re-open the leg-merge under-block) + _VERB_MSG_BODY (STOPS at
        #     the first UNQUOTED ;/&&/|/newline, so an executing tail stays OUTSIDE the span
        #     and is caught). merge / stash push / stash store / notes are anchored on
        #     _MSG_FLAG_ANCHOR (their SOLE value-taking --m* is --message, verified via
        #     `git <verb> -h`). git cherry-pick / git revert are DELIBERATELY EXCLUDED:
        #     their -m is --mainline <parent-number> (a NUMBER, not a message) — treating
        #     them as message carriers would be wrong; a real destructive tail after them
        #     stays caught via leg-locality. $()/backtick preserve rides on
        #     _keep_carrier_value.
        _git_merge_span = (
            r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}merge\b" % _MAX_GLOBAL_FLAG_TOKENS
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _git_merge_span,
            lambda mm: _strip_flag_values(
                mm.group(0), _MSG_FLAG_ANCHOR, _keep_carrier_value
            ),
            result,
        )
        # git stash push / store — both take -m/--message.
        _git_stash_flag_span = (
            r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}stash\s+(?:push|store)\b" % _MAX_GLOBAL_FLAG_TOKENS
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _git_stash_flag_span,
            lambda mm: _strip_flag_values(
                mm.group(0), _MSG_FLAG_ANCHOR, _keep_carrier_value
            ),
            result,
        )
        # git stash save <message> — the (deprecated) message is a POSITIONAL; save's
        # non-message args are ALL boolean flags, so the anchor consumes `save` + any run
        # of boolean flags + whitespace, and the dq/sq/VALUE-TOKEN arms strip the first
        # value token (the positional message). Bare `git stash save` (no message) has no
        # trailing value -> the anchor's trailing `\s+`+value never matches -> no misfire.
        _git_stash_save_span = (
            r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}stash\s+save\b" % _MAX_GLOBAL_FLAG_TOKENS
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _git_stash_save_span,
            lambda mm: _strip_flag_values(
                mm.group(0), r"(save(?:\s+-[-\w]+)*\s+)", _keep_carrier_value
            ),
            result,
        )
        # git notes add / append (-m/--message; an optional `--ref <ref>` global may
        # precede the verb).
        _git_notes_span = (
            r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}notes\s+(?:--ref(?:=|\s+)\S+\s+)?(?:add|append)\b"
            % _MAX_GLOBAL_FLAG_TOKENS
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _git_notes_span,
            lambda mm: _strip_flag_values(
                mm.group(0), _MSG_FLAG_ANCHOR, _keep_carrier_value
            ),
            result,
        )

    # 8. Strip HTTP-client request-body flag VALUES (curl / wget / gh api).
    #    The #1061 widening (host-agnostic `.*git/refs`) and the #1063
    #    `.*branches/.*/protection` arms would OTHERWISE fire on the destructive
    #    path text when it merely appears inside a QUOTED data-body argument
    #    (`curl -X POST .../log -d 'msg=touched git/refs/heads/x'`) — an over-block
    #    of a faithful command (the #1037 hard-constraint). A faithful API ref /
    #    protection mutation ALWAYS carries the destructive resource in the URL
    #    PATH (REST convention — the resource IS the URL), NEVER the request body,
    #    so stripping ONLY the data-body VALUE is zero-under-block: a path-resident
    #    target can never be removed (the PATH-vs-BODY invariant).
    #
    #    Surface-scoped to a curl / wget / gh-api command SPAN so a `git push -d
    #    <ref>` (git's `-d` = --delete, the ref is a positional we DO gate) is never
    #    mis-stripped — the span anchors only on an HTTP-client head, and git is
    #    absent from the anchor. TWO value-forms are handled:
    #      (a) direct-value body flags — `FLAG <quoted-value>`: curl/wget
    #          -d/--data[-raw|-binary|-ascii|-urlencode]/--body-data/--post-data.
    #      (b) KEY=VALUE field flags — `FLAG <key>=<quoted-value>`: gh-api
    #          --field/--raw-field/-f/-F (and curl -F form data). Strip the value
    #          AFTER the `=`, keeping `flag key=`.
    #    Surface-awareness falls out of the patterns: curl's bare boolean `-f`
    #    (--fail) is followed by no `key=<quoted>` token, so form (b) never matches
    #    it and a `curl -f -X DELETE .../git/refs/...` still gates.
    #    EVERY flag token is KEPT (only the value is replaced) so the implicit-POST
    #    lookaheads (`(?=.*(?:--data...|-f|--field|...)\s)`) still fire on a genuine
    #    implicit-POST. Same execution-routing guards as carriers 3/5/7: skip when
    #    piped/process-sub to a shell; the double-quoted arms preserve a value
    #    containing command-substitution `$(`/backtick (it would execute).
    #    (httpie body params are POSITIONALS, not flags, so they are out of scope —
    #    non-key=value spellings (:= JSON literals, bare quoted items) survive, so a
    #    quoted git literal can still FP via the match-anywhere git arms: rarer,
    #    fails-safe (gates AND mints — no permanent block), pre-existing, un-fixed.)
    if not piped_to_shell and not process_sub_to_shell:
        # The HTTP-client command span: from a curl/wget/gh-api head up to the first
        # UNQUOTED shell separator. Quote-aware body (balanced quotes consumed
        # atomically; disjoint first chars → linear, no ReDoS) — identical shape to
        # carrier 7's span. The URL positional and the method flag live in this span
        # but are never matched by the body-flag patterns below (which require a body
        # FLAG before the value), so they always survive. The gh-api head uses the
        # TOLERANT `_GH_API_PREFIX` (same as the read floor) so a `gh -R o/r api ...`
        # global-flag spelling's body IS stripped — else the #1037 body-mention
        # over-block re-opens for that spelling (the strip would not run).
        _http_client_span = (
            r"(?:\bcurl\b|\bwget\b|" + _GH_API_PREFIX + r")"
            + _VERB_MSG_BODY
        )
        # Direct-value body flags (form a).
        _data_flag = r"(?:--data(?:-(?:raw|binary|ascii|urlencode))?|--body-data|--post-data|-d)"
        # KEY=VALUE field flags (form b). The `<key>=` requirement is what makes the
        # strip surface-aware (curl's boolean -f never has a key= after it).
        _field_flag = r"(?:--field|--raw-field|-F|-f)"

        def _strip_http_body_span(span_match: re.Match) -> str:
            span = span_match.group(0)

            # CONTENTS-API EXCEPTION (PATH-vs-BODY invariant boundary). The
            # `.*contents/.*(?:main|master)` arm reads its destructive target — the
            # branch being written — from the REQUEST BODY (`-d '{"branch":"master"}'`)
            # or a `?branch=` query, NOT the URL path (the contents API path is
            # `/contents/{filepath}`, branch-less). This is the ONE gated arm whose
            # signal is body-resident; stripping its body would REMOVE the main/master
            # gating signal → an UNDER-BLOCK. The contents arm must be left UNCHANGED
            # (signal is body-resident), so preserve a contents-API span verbatim. Detected
            # per-span (a compound's `/log` span is still stripped). git/refs and
            # branches/protection targets are path-resident, so their bodies stay strippable.
            # IGNORECASE to match the case-insensitive contents READ arm
            # (`contents/.*(?:main|master)`, re.IGNORECASE): a `Contents/` (any case)
            # span carries its main/master gating signal in the BODY, so it must be
            # preserved from stripping in ANY case — else the #1096 unquoted body
            # strip removes the signal for a capital-case spelling -> under-block.
            if re.search(r"contents/", span, re.IGNORECASE):
                return span

            def _keep_flag_dq(m: re.Match) -> str:
                # group(1) = the flag (+ key= for form b) up to the opening quote.
                if _has_command_substitution(m.group(0)):
                    return m.group(0)  # value contains $()/backtick → executes; keep
                return m.group(1) + "'STRIPPED'"

            # Body flag VALUES via the SHARED quote-safe strip (#1118 re-model, FIX-A).
            # Each call does 3 arms (dq / sq / unquoted VALUE-TOKEN); the unquoted arm now
            # consumes a COMPLETE quote-balanced token, so an embedded-quote body value
            # (`-f k=x"y z"`, a jq-ish `-d '...' ` payload) can no longer leave a dangling
            # quote that mis-pairs in _mask_shell_quotes and MERGES legs — the pre-existing
            # carrier-8 under-block (#1118 review). This closes the endpoint-decoy the
            # quoted-only arms miss (`-f note=pulls/5/merge`, `-d body`) + the #1037
            # unquoted-body read-floor over-block, over-block-safely: the arm is anchored on
            # the body-flag(+key=) prefix, so the URL POSITIONAL endpoint (never preceded by
            # such a prefix) is never matched. SPACE-form separator ONLY here — carrier-8's
            # `=`-joined-flag completeness (`--field=k=v`) is a DIFFERENT, deferred issue
            # (#1125); this commit fixes ONLY carrier-8's quote-safety.
            # (a) direct-value body flags -d/--data*; (b) key=value field flags -f/-F/--field.
            span = _strip_flag_values(span, r"(" + _data_flag + r"\s+)", _keep_flag_dq)
            span = _strip_flag_values(
                span, r"(" + _field_flag + r"\s+[\w.\-]+=)", _keep_flag_dq
            )
            return span

        result = re.sub(_http_client_span, _strip_http_body_span, result)

    # 9. Strip gh-api CLIENT-SIDE OUTPUT-SELECTOR + non-target flag VALUES (#1118), via
    #    the SHARED quote-safe _strip_flag_values helper.
    #    Carrier 8 removes the merge/git-refs FP substring from request-BODY values, but a
    #    gh-api command's output selectors (--jq/-q jq filter, --template/-t Go template)
    #    and other non-target value flags (-H/--header, --input body-file name, --hostname,
    #    -p/--preview) still carry it, so a graphql/REST READ whose --jq names a response
    #    field like `mergeStateStatus` is OVER-BLOCKED by the .*merge implicit-POST arm.
    #    These VALUES can NEVER be the request ENDPOINT or METHOD (jq/template run on the
    #    RESPONSE client-side; a header is request metadata; --input names a local file; a
    #    hostname/preview name is not the resource — the endpoint is always the URL
    #    positional), so stripping ONLY the value is zero-under-block (the CLIENT-vs-SERVER
    #    analogue of carrier 8's PATH-vs-BODY invariant). gh-api's flag vocabulary is FINITE
    #    + DOCUMENTED, so this surface is BOUNDED (unlike curl/wget's unbounded value-flag
    #    vocabulary -> WON'T-FIX by construction, #1098). Scoped to a gh-api span ONLY (its
    #    OWN _GH_API_PREFIX anchor, NARROWER than carrier 8's shared curl/wget/gh-api span),
    #    and the FORM-AWARE `_selector_flagsep` is token-boundary anchored (`(?<!\S)`), so
    #    -q/-t/-H/-p can never mis-strip curl -t (--telnet-option), wget -t (--tries),
    #    gh pr create -t (--title, carrier 7), or a `-q` mid-token. Every flag token is KEPT
    #    (only the value is replaced) so the implicit-POST lookaheads still fire on a genuine
    #    write (a real mutation via --input to a path-resident endpoint keeps its --input
    #    flag and stays HELD; its destructive target lives in the URL positional, never
    #    stripped). CONTENTS-API early-return mirrors carrier 8: never touch a contents/ span
    #    (its main/master gating signal is body/positional-resident). --cache is OUT (its
    #    duration grammar provably cannot carry the substring).
    #
    #    QUOTE-SAFETY (#1118 re-model): the value strip goes through the SHARED
    #    _strip_flag_values (quote-BALANCED _VALUE_TOKEN) + the FORM-AWARE separator (space /
    #    =-long / =-short / attached-short). The PRIOR shipped carrier 9 used a
    #    non-quote-aware unquoted arm (`[^\s'"]\S*`) + a `\s+`-only separator; its earlier
    #    "additive-pure / cannot open a new under-block" claim was FALSIFIED by the #1118
    #    review — on an embedded-quote value it emitted a DANGLING quote that mis-paired in
    #    _mask_shell_quotes, MERGED legs, and regressed the guard in BOTH directions (SEC-1
    #    over-block + SEC-2 under-block). This is NOT +N/-0; correctness is proven by
    #    BIDIRECTIONAL base-vs-HEAD-vs-PATCH testing, never byte-diff. Same execution-routing
    #    guards as carriers 3/5/7/8: skip when piped/process-sub to a shell; the double-
    #    quoted arm preserves a value containing command substitution `$(`/backtick.
    if not piped_to_shell and not process_sub_to_shell:
        # The gh-api command span: from a gh-api head up to the first UNQUOTED shell
        # separator. Quote-aware body (balanced quotes consumed atomically; disjoint
        # first chars -> linear, no ReDoS) — identical shape to carriers 7/8, but
        # anchored on gh-api ONLY (no curl/wget head), so -q/-t/-H/-p can never be
        # mis-read as a curl/wget short flag. The URL positional and the -X/--method
        # flag live in this span but are never matched by the selector-flag arms below
        # (which require a selector FLAG directly before the value), so they survive.
        _gh_api_selector_span = (
            r"(?:" + _GH_API_PREFIX + r")"
            + _VERB_MSG_BODY
        )
        # FORM-AWARE, token-anchored separator (#1118 re-model, FIX-B). gh (cobra/pflag)
        # accepts a flag value four ways: space (`--jq x`), =-long (`--jq=x`), =-short
        # (`-q=x`), attached-short (`-qx`). Long flags require a separator; short flags may
        # attach. `(?<!\S)` anchors the flag at a token boundary, so `-q`/`-t`/`-H`/`-p`
        # never match inside a positional (`some-q-endpoint`) or an already-stripped value.
        # Long forms BEFORE short forms (so `--template` is not read as `-t` + `emplate`).
        # Boolean flags (`--paginate`, `--slurp`, …) carry no value and are absent from the
        # set. Wrapped in ONE capturing group at the call site (see _strip_flag_values).
        _selector_flagsep = (
            r"(?<!\S)(?:"
            r"(?:--jq|--template|--header|--input|--hostname|--preview)(?:\s+|=)"
            r"|(?:-q|-t|-H|-p)(?:\s+|=)?"
            r")"
        )

        def _keep_selector_dq(m: re.Match) -> str:
            # group(1) = the flag(+separator) captured by the wrapped _selector_flagsep.
            # Preserve a value that contains command substitution ($()/backtick) — it
            # would EXECUTE and must stay visible to the danger arms (same shape as carrier
            # 8's _keep_flag_dq; both are passed as keep_fn to the shared _strip_flag_values).
            if _has_command_substitution(m.group(0)):
                return m.group(0)
            return m.group(1) + "'STRIPPED'"

        def _strip_gh_api_selectors(span_match: re.Match) -> str:
            span = span_match.group(0)
            # CONTENTS-API exception (IGNORECASE, mirrors carrier 8): a contents span
            # carries its main/master gating signal in the body/positional, so preserve
            # the span verbatim (an output selector on a contents span is a rare, fail-
            # safe pre-existing residual, not in #1118 scope).
            if re.search(r"contents/", span, re.IGNORECASE):
                return span
            # Selector flag VALUES via the SHARED quote-safe strip (FIX-A + FIX-B). One call
            # does all three arms (dq / sq / unquoted VALUE-TOKEN) across all four value-
            # attachment forms. The unquoted arm consumes a COMPLETE quote-balanced token, so
            # an embedded-quote selector value (a jq `.a+" "+.b`, a Go template
            # `{{.n}}" "{{.t}}`, `-q x"y z"`) can no longer leave a dangling quote that merges
            # legs (SEC-1/SEC-2). Anchored on the selector-flag(+separator) prefix, so the URL
            # POSITIONAL endpoint (never preceded by such a prefix) is never matched -> a real
            # destructive target in the path is never removed (over-block-safe).
            span = _strip_flag_values(
                span, r"(" + _selector_flagsep + r")", _keep_selector_dq
            )
            return span

        result = re.sub(_gh_api_selector_span, _strip_gh_api_selectors, result)

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


def _shell_tokenize(command: str) -> list[str] | None:
    """P1: quote-aware shell-word tokenizer (shlex.split posix=True, comments=False) —
    strips single/double quotes, processes escapes, keeps a quoted-value span as ONE
    token. Returns the token list on success, or None on ValueError (unbalanced /
    unterminated quote). On None the callers ABSTAIN (extract_privileged_flags falls back
    to `split()`; _flag_condition_danger_op returns None) and let the literal floor
    (DANGEROUS_PATTERNS) decide — so an untokenizable command is dangerous only if the
    floor matches, never dangerous merely because it failed to tokenize. shlex leaves
    $ / $() / backtick LITERAL (no expansion); under the honest-mistake model that is
    acceptable — runtime $-expansion is explicitly out of scope (the hook only ever sees
    the pre-expansion literal an honest agent typed)."""
    try:
        return shlex.split(command, posix=True, comments=False)
    except ValueError:
        return None


def _mask_shell_quotes(command: str) -> str:
    """P2: bounded same-length quote-state scanner. Returns a copy with quoted spans
    (delimiters + contents) — BOTH '...' and "..." — replaced by spaces, preserving
    out-of-quote structure at identical offsets (P3 operator detection: a separator
    inside EITHER quote is not a real separator). FAILS TOWARD UNMASKED: a `\\`-escaped
    quote (outside quotes) never opens a span, and a mis-paired / unterminated quote
    leaves the REST unmasked (visible) — so an operator/metachar can only OVER-block,
    never under-block (the #1037-CLASS-1 closure: ambiguity never HIDES danger).
    Identity on an unquoted command (constraint a)."""
    out = list(command)
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c == "\\":
            i += 2  # escaped char (outside a quote) — next char is literal, not a delim
            continue
        if c == "'" or c == '"':
            j = i + 1
            closed = False
            while j < n:
                if c == '"' and command[j] == "\\":
                    j += 2  # \\-escape is honored inside "..." (not inside '...')
                    continue
                if command[j] == c:
                    closed = True
                    break
                j += 1
            if not closed:
                break  # unterminated quote → FAIL TOWARD UNMASKED (leave rest visible)
            for k in range(i, min(j + 1, n)):
                out[k] = " "
            i = j + 1
            continue
        i += 1
    return "".join(out)


def _normalize_line_continuations(command: str) -> str:
    """P0 (shell-semantic substrate SSOT): join bash line-continuations
    (`\\<newline>` → space) BEFORE tokenization, so a `\\<newline>`-split flag
    (`gh pr close 5 \\<newline>-d`) becomes a clean separate token instead of a fused
    `\\n-d` that the flag scan would miss (the security line-continuation under-block).
    Routed through every floor call site + the new substrate so mint and read join
    lines identically (mint==read by construction)."""
    return command.replace("\\\n", " ")


# Benign continuation / redirect terminator for the positional target extractors
# (_extract_force_push_target_ref / _extract_branch_name). Matches a compound
# operator (`&&`, `||`, `|&`, `;`, `&`, `|`, newline — the _COMPOUND_OPS_RE set)
# OR a redirect START. The redirect arm is `(?<!\S)\d+[<>]|[<>]`: a bare `<`/`>` is
# ALWAYS a redirect (fd defaults), AND a leading fd-NUMBER (`2>`, `22>`) is a
# redirect prefix ONLY when it is a standalone token — i.e. NOT preceded by a
# non-whitespace char. This mirrors bash's IO_NUMBER rule: digits GLUED to the
# preceding word are PART OF THE WORD, not an fd-number. So `git push origin
# main2>log` is the bash WORD `main2` plus a `>log` redirect (the refspec is
# `main2`, NOT `main`); only a whitespace-preceded `2>` (e.g. `main 2>&1`) is an
# fd-redirect that truncates. The `(?<!\S)` lookbehind also keeps the scan LINEAR
# on a long digit run — it prunes the redundant in-run start positions that a bare
# `\d*` would re-scan (the O(n^2) catastrophic-backtracking a `\d*[<>]` exhibits).
_BENIGN_TERMINATOR_RE = re.compile(r"&&|\|\||\|&|;|&|\||\n|(?<!\S)\d+[<>]|[<>]")

# Process-substitution marker (`>(`/`<(`, allowing one optional space). An UNQUOTED
# procsub is never an honest force-push/branch-delete form: bash treats
# `git push --force origin main >(cmd)` as a MULTI-ref push (`>(cmd)` expands to an
# extra `/dev/fd/N` positional), and `... > >(cmd)` redirects stdout into the
# procsub FIFO. _executable_prefix ABSTAINS when this appears, rather than
# truncating at the redirect and mis-deriving a single-ref target. Scanned on the
# same _mask_shell_quotes view, so a quoted `"a>(b)"` is inert (never an over-block).
_PROCSUB_MARKER_RE = re.compile(r"[<>] ?\(")


def _executable_prefix(command: str) -> str | None:
    """The command truncated at the first UNQUOTED compound-op-or-redirect.

    Returns the leading executable span — everything before the first benign
    continuation / redirect detected on the same-length `_mask_shell_quotes` view
    — or the whole command when there is no such terminator. A quoted metachar
    (`"weird>name"`) is masked to spaces on that view, so only an UNQUOTED
    operator / redirect bounds the prefix. Returns None (the caller treats None as
    an ABSENT target and REFUSES — fail-OPEN to the existing safe over-block) iff
    EITHER the quote state is ambiguous (an unbalanced / unterminated quote makes
    `_shell_tokenize` fail) OR an unquoted process-substitution marker (`>(`/`<(`)
    is present (defense-in-depth — never an honest destructive form, and it makes a
    single-ref target ambiguous). An adversarial quote-elided command is out of
    scope and is never authorized, only ever over-blocked.
    """
    if _shell_tokenize(command) is None:  # unbalanced/unterminated quote -> abstain
        return None
    masked = _mask_shell_quotes(command)  # same-length; quoted metachars -> spaces
    # Process-substitution defense-in-depth: an unquoted `>(`/`<(` is never an
    # honest force-push/branch-delete form (bash makes a single-ref target ambiguous
    # -> a multi-ref push, or redirects into a FIFO). Abstain rather than truncate
    # at the redirect and mis-derive the target. Over-block direction only.
    if _PROCSUB_MARKER_RE.search(masked):
        return None
    terminator = _BENIGN_TERMINATOR_RE.search(masked)
    return command[: terminator.start()] if terminator else command


# -----------------------------------------------------------------------------
# P4 — op-agnostic quote-aware flag normalizer + per-op danger CONDITIONS.
#
# Generalizes the extract_privileged_flags cluster-walk into a SURFACE-keyed
# normalizer fed by P1 tokens (quotes already stripped, so a quoted `"--admin"`
# normalizes the same as a bare one). The SAME short differs by tool surface
# (gh `-d` = --delete-branch; git `-d` = --delete), so the spec is keyed by
# SURFACE. Danger is then a boolean CONDITION over the normalized set, ADDED to
# the literal DANGEROUS_PATTERNS floor as a UNION arm (INV-AU: additive only — a
# normalizer mis-parse can only fail-to-ADD a detection → over-block, never
# under-block, because the literal floor still gates underneath).
# -----------------------------------------------------------------------------

# Per-surface flag spec: alias -> (canonical token, takes_value). A superset of
# PRIVILEGED_FLAGS that ALSO carries the danger-relevant booleans the per-op
# conditions test (-D / --delete / --force / --force-with-lease). The value-taking
# entries (-R / --repo) are listed so a cluster like `-Rd val` parses correctly
# (-R consumes the rest of the cluster as its value, so the trailing `d` is NOT
# mis-read as --delete-branch). Aliases AGREE with PRIVILEGED_FLAGS so the danger
# arm and the #1042 bind never disagree on a spelling. Unicode look-alike dashes
# (U+2010 / U+2212) are deliberately ABSENT: an ASCII-only `startswith('-')`
# leaves them unbound — gh/git reject them byte-exact, so they confer no privilege
# and folding-to-ASCII would over-block a flag the tools simply ignore.
_FLAG_SPEC: dict[str, dict[str, tuple[str, bool]]] = {
    "gh": {
        "--admin": ("--admin", False),
        "-d": ("--delete-branch", False),
        "--delete-branch": ("--delete-branch", False),
        "-R": ("--repo", True),
        "--repo": ("--repo", True),
        "--match-head-commit": ("--match-head-commit", True),
    },
    "git": {
        "-D": ("-D", False),
        "-d": ("--delete", False),
        "--delete": ("--delete", False),
        "-f": ("--force", False),
        "--force": ("--force", False),
        "--force-with-lease": ("--force-with-lease", False),
        "--no-verify": ("--no-verify", False),
        # remote-mass-delete (#1062b) danger-condition booleans — present so
        # `_normalized_flags` can SEE them for the mass-delete recognition arm. Like
        # -D/--force, they are op-trigger booleans in _FLAG_SPEC but EXCLUDED from
        # PRIVILEGED_FLAGS (the #1042 set-equality bind is untouched).
        "--mirror": ("--mirror", False),
        "--prune": ("--prune", False),
    },
}

# Boolean-flag values that DISABLE the flag: `--admin=false` is the SAFE form, so
# it does NOT confer the privilege / satisfy a danger condition. Any OTHER value
# (or none) binds (fail-toward-binding on an unrecognized value = over-block-safe).
_NEGATED_FLAG_VALUES = frozenset({"false", "0", "no"})


def _normalized_flags(tokens: list[str], surface: str) -> set[str]:
    """P4: canonicalize a P1 token list into the SET of flags PRESENT, across every
    spelling (short / long / clustered / `=`-joined / attached-value), keyed by the
    tool SURFACE ('gh' / 'git'). Booleans → bare canonical (`--delete-branch`);
    value-takers → `--canonical=value`. An `=false`/`=0`/`=no` on a boolean NEGATES
    it (omitted — the safe disable form). Mirrors the extract_privileged_flags
    cluster-walk so the danger arm and the #1042 bind agree on every spelling.
    Over-block-safe: an unrecognized token is skipped (never mis-bound)."""
    spec = _FLAG_SPEC.get(surface, {})
    if not spec:
        return set()
    found: set[str] = set()
    i, n = 0, len(tokens)
    while i < n:
        token = tokens[i]
        if not token.startswith("-") or token in ("-", "--"):
            i += 1
            continue
        if token.startswith("--"):
            flag_part, has_eq, value = token.partition("=")
            entry = spec.get(flag_part)
            if entry is None:
                i += 1
                continue
            canonical, takes_value = entry
            if takes_value:
                if has_eq:                       # --repo=value
                    found.add(f"{canonical}={value}")
                    i += 1
                elif i + 1 < n:                  # --repo value
                    found.add(f"{canonical}={tokens[i + 1]}")
                    i += 2
                else:                            # --repo (value missing; degenerate)
                    found.add(canonical)
                    i += 1
            else:
                # boolean: an explicit `=false`/`=0`/`=no` DISABLES it → do not bind.
                if not (has_eq and value.lower() in _NEGATED_FLAG_VALUES):
                    found.add(canonical)
                i += 1
            continue
        # short cluster (single dash): a per-character walk matching the privileged
        # extractor's — a value-taking short consumes the REST of the cluster (or the
        # next token) and stops, so no bound short is dropped regardless of ordering.
        cluster = token[1:]
        consumed_next = False
        j = 0
        while j < len(cluster):
            entry = spec.get("-" + cluster[j])
            if entry is None:
                j += 1
                continue
            canonical, takes_value = entry
            if takes_value:
                remainder = cluster[j + 1:]
                if remainder.startswith("="):    # `-R=value`
                    remainder = remainder[1:]
                if remainder:                    # `-Rvalue`
                    found.add(f"{canonical}={remainder}")
                elif i + 1 < n:                  # `-R value`
                    found.add(f"{canonical}={tokens[i + 1]}")
                    consumed_next = True
                else:                            # `-R` (value missing; degenerate)
                    found.add(canonical)
                break
            found.add(canonical)                 # boolean short; keep walking
            j += 1
        i += 2 if consumed_next else 1
    return found


def _flag_condition_danger_op(command: str) -> str | None:
    """P4 union arm: classify the FIRST EXECUTABLE LEG of `command` by a quote-aware
    NORMALIZED-FLAG danger CONDITION across every flag spelling, returning the
    op-class ("close" / "branch-delete" / "force-push" / "remote-ref-delete" /
    "remote-mass-delete") iff a condition fires, else None. FIRST-LEG-ANCHORED
    (extending the conservative-RECOGNITION posture to this
    arm): every surface consulted here — the token list, the coarse-shape prefixes,
    and the extractor inputs — derives from `_executable_prefix(command)`, because
    deriving FLAGS from the whole command while POSITIONALS came from the first
    executable leg let a force/delete flag in a benign CONTINUATION leg mislabel a
    benign first-leg op (the #1078 cross-leg flag leak). The coarse op-shape (which
    subcommand) is matched with the SAME shared prefixes the literal floor uses; the
    danger test is then a boolean condition over `_normalized_flags`. ADDITIVE over
    the literal floor (INV-AU): an unparseable command / mis-parse can only FAIL to
    return an op here (this arm ABSTAINS; the literal floor still decides), never
    re-open an under-block. The coarse shape only SCOPES which condition runs — a
    false coarse-match whose condition does not hold returns None (over-block-safe)."""
    prefix = _executable_prefix(command)
    if prefix is None:
        # Unbalanced quote OR process substitution → abstain; the literal floor
        # decides. Procsub is never an honest destructive form (the helper's own
        # rationale) — an exotic procsub+cluster combo is an accepted under-block.
        return None
    tokens = _shell_tokenize(prefix)
    if tokens is None:
        return None  # unparseable → this arm abstains; the literal floor decides (honest-mistake: no metachar catch-all)
    # close --delete-branch — covers `-d`, clustered `-cd`, `--delete-branch`; the
    # literal floor matches ONLY the spelled-out `--delete-branch` (the #2 gap).
    if _GH_PR_CLOSE_RE.search(prefix):
        if "--delete-branch" in _normalized_flags(tokens, "gh"):
            return "close"
    # git branch force-delete — covers `-D`, `-Df`, `-fD`, `--delete -f`/`--force`
    # in any order; the literal floor matches ONLY `-D\b` / `--delete --force` /
    # `--force --delete` (the #4 gap).
    if re.search(_GIT_PREFIX + r"branch\b", prefix):
        gf = _normalized_flags(tokens, "git")
        if "-D" in gf or ("--delete" in gf and "--force" in gf):
            return "branch-delete"
    # git push --force — covers clustered short forms; `--force-with-lease` is the
    # SAFE exclusion (a non-history-rewriting push). Redundant with the literal floor
    # today (`-[a-zA-Z]*f` already catches the clusters) but kept for op-class parity.
    if re.search(_GIT_PREFIX + r"push\b", prefix):
        gf = _normalized_flags(tokens, "git")
        if "--force" in gf and "--force-with-lease" not in gf:
            return "force-push"
        # remote-ref-delete (#1062a) — union-arm-only recognition (lead Q2: NO
        # literal DANGEROUS_PATTERNS ref-delete arm). Recognize IFF a SINGLE
        # deletable ref is extractable; recognition⟺mintability by construction
        # (the SAME predicate feeds detect via the fallback AND the is_dangerous
        # union), so this op can NEVER be #1064 gated-but-unmintable. A multi-ref /
        # implicit-current / ambiguous form yields None here → not recognized → the
        # mass arm below or the literal floor decides. Tried FIRST = the single-ref-
        # extractability BOUNDARY discriminator: mass only runs when this returns None.
        # Both extractors are fed `prefix` for single-surface coherence: each
        # re-derives `_executable_prefix` internally (idempotent on a prefix), so
        # this is behavior-identical — but the arm then has exactly ONE surface.
        if _extract_remote_ref_delete_target(prefix) is not None:
            return "remote-ref-delete"
        # remote-mass-delete (#1062b) — mass forms (--mirror/--prune/multi-ref delete),
        # recognized IFF a normalized mass-target tuple is extractable (the extractor
        # itself defers to remote-ref-delete for a single ref, so no double-classify).
        # Recognition⟺mintability by construction → #1064-impossible (implicit-remote
        # included via the definite \x00implicit marker).
        if _extract_mass_delete_target(prefix) is not None:
            return "remote-mass-delete"
    return None


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

    # Normalize bash line continuations (\<newline>) via the shared P0 SSOT before
    # any matching (so this floor + the substrate join lines identically).
    command = _normalize_line_continuations(command)
    stripped = _strip_non_executable_content(command)
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(stripped):
            return True
    # Literal force-push arms, matched PER-LEG (#1082): leg boundaries come from
    # _slice_stripped_legs over this SAME `stripped` text — identical strip
    # provenance to _split_into_legs (normalize + strip, above), so read-floor legs
    # and substrate legs can never diverge, and the strip is not recomputed. An arm
    # fires iff push and the force-class flag co-occur within ONE leg, in ANY leg
    # position (the match-anywhere purpose, per leg). The legs are computed ONCE
    # here and shared by all four literal-arm loops below (force-push, close, API,
    # branch-delete).
    legs = _slice_stripped_legs(stripped)
    for _leg in legs:
        if any(arm.search(_leg) for arm in _FORCE_PUSH_LITERAL_ARMS):
            return True
    # Literal close arms, matched PER-LEG (#1087): same leg substrate and strip
    # provenance as the force-push loop above. An arm fires iff `gh pr close` and
    # `--delete-branch` co-occur within ONE leg — so the ambiguous cross-leg
    # multi-close is is_dangerous=False here, the mint write-gate refuses (no token),
    # and the escalated-single laundering channel is structurally closed.
    for _leg in legs:
        if any(arm.search(_leg) for arm in _CLOSE_LITERAL_ARMS):
            return True
    # Literal API danger arms, matched PER-LEG (#1086): same leg substrate and strip
    # provenance as the loops above. An arm fires iff the API client, its mutating
    # method (or implicit-POST body flag), and the target endpoint co-occur within ONE
    # leg — so a method/body-flag token in a benign continuation leg no longer
    # over-blocks the compound, while a same-leg dangerous API call still gates. The
    # negative-lookahead arms operate within-leg (correct exclusion/inclusion direction).
    for _leg in legs:
        if any(arm.search(_leg) for arm in _API_LITERAL_ARMS):
            return True
    # Literal branch-delete arms, matched PER-LEG (#1094): same leg substrate and
    # strip provenance as the loops above. An arm fires iff `git branch` and the
    # force-delete flag co-occur within ONE leg — a stray `-D` / `--delete --force`
    # token in a benign continuation leg no longer gates the compound (and the
    # formerly-mintable ambiguous compound is is_dangerous=False, so the mint
    # write-gate refuses → no token → the laundering substrate is structurally
    # closed), while a same-leg force-delete still gates in ANY leg position.
    # Clustered/split flag spellings remain the union arm's job (below).
    for _leg in legs:
        if any(arm.search(_leg) for arm in _BRANCH_DELETE_LITERAL_ARMS):
            return True
    # ADDITIVE union arm (INV-AU): a quote-aware normalized-flag danger CONDITION across
    # every flag spelling the literal floor misses — `-d`/`-cd` close delete, `-Df`/`-fD`/
    # `--delete -f` branch force-delete. Runs on the STRIPPED surface (same as the floor)
    # so a flag spelled inside a comment / heredoc / echo / var-assignment does NOT false-
    # trigger; the shlex tokenizer keeps a quoted argument as ONE token so a flag inside a
    # quoted value is never read as a flag. The literal floor stays the fail-closed default.
    if _flag_condition_danger_op(stripped) is not None:
        return True
    return False


# A plain `rm` head token at a compound leg's start. DELIBERATELY rm-SPECIFIC and used
# ONLY by the compound-leg count below — NOT a general dangerous-op detector (dd/mkfs/
# shred/etc. are out of scope under the honest-mistake model) and NOT part of
# is_dangerous_command (so a bare `rm -rf /` and a pure-rm chain stay is_dangerous=False
# and the guard never gates them — see the rm-exception note in the module THREAT MODEL).
# Matches a literal `rm` at the leg head only; no obfuscation-chasing — not `/bin/rm`,
# `r''m`, `$(echo rm)`, or aliases.
_RM_HEAD_RE = re.compile(r"\s*rm(?=\s|$)")


def _leg_is_destructive(leg: str) -> bool:
    """Count a compound leg as destructive if it is a recognized git/gh-destructive op
    (``is_dangerous_command``) OR its head command is a plain ``rm`` (the rm-exception).

    The rm arm is the ONE deliberate non-git/gh case: an honest agent chaining a real
    destructive git/gh op WITH a file-removing ``rm`` (``gh pr merge 5 && rm -rf /``) is
    exactly the multi-destructive mistake the compound refuse exists to catch. It is
    rm-specific by design; do NOT generalize it to other filesystem-destroying tools.
    """
    return is_dangerous_command(leg) or _RM_HEAD_RE.match(leg) is not None


def _slice_stripped_legs(stripped: str) -> list[str]:
    """Slice an ALREADY-STRIPPED command into its shell-operator-separated legs
    (always >=1 leg). The mask + FD-neutralize + slice core of the leg-boundary
    SSOT: `_split_into_legs` wraps this with normalize + strip, and callers that
    already hold the stripped text can slice directly, so leg boundaries are
    computed from ONE substrate without re-stripping.

    Operators are detected on the P2-masked + FD-neutralized view so an operator
    INSIDE a quoted arg (`--subject "a; b"`) or an FD / and-redirect (`2>&1`,
    `&>`, `>|`) is NOT a separator. Each FD-redirect is replaced by an EQUAL-LENGTH
    run of spaces (NOT a single space) so the masked view stays SAME-LENGTH as
    `stripped` and each operator's offsets map 1:1 back to the ORIGINAL legs (which
    carry the real flag spellings the callers classify). A single-space collapse
    would shrink the view and mis-slice the legs after a multi-char redirect (e.g.
    `2>&1 | rm -rf ~`). The leg slices are taken from `stripped`, never the masked
    `view` — the view exists ONLY to locate the operator offsets.
    """
    view = _FD_REDIRECT_RE.sub(
        lambda m: " " * len(m.group()), _mask_shell_quotes(stripped)
    )
    legs, last = [], 0
    for m in _COMPOUND_OPS_RE.finditer(view):
        legs.append(stripped[last : m.start()])
        last = m.end()
    legs.append(stripped[last:])
    return legs


def _split_into_legs(command: str) -> list[str]:
    """Split a command into its shell-operator-separated legs (always >=1 leg).

    The single SSOT for leg boundaries: both is_compound_destructive_command (the
    >=2-destructive-leg refuse) AND _single_destructive_leg (the read-side
    single-leg isolation) consume this, so the two can never see divergent leg
    boundaries (the #720/#878 divergence class). A command with no shell operator
    yields a one-element list (the whole stripped command). Operator-detection
    mechanics (quote masking, equal-length FD neutralization, slicing from the
    stripped text) live in `_slice_stripped_legs`, the shared slicing core.
    """
    normalized = _normalize_line_continuations(command)
    stripped = _strip_non_executable_content(normalized)
    return _slice_stripped_legs(stripped)


def _single_destructive_leg(command: str) -> str | None:
    """The UNIQUE is_dangerous_command leg of a command, or None.

    Returns the single destructive gh/git leg when EXACTLY one leg is dangerous;
    None when 0 legs are dangerous, or — at the read call site, unreachably — when
    >=2 are (is_compound_destructive_command REFUSES that upstream, at
    check_merge_authorization, BEFORE the read seam). The read seam consumes this
    as TIER 1 of a two-tier bind-surface fallback: None here falls through to
    `_single_detectable_leg` (tier 2 — the EMERGENT-danger case, where whole-
    command danger comes from a cross-leg lookahead and no leg is dangerous in
    isolation), and only then to the WHOLE command (the existing over-binding
    scan = the safe over-block direction). The never-silently-narrow guard is
    about AMBIGUITY and is preserved across both tiers: a fail-toward-unmasked
    quote split, a parse failure, or a non-unique leg can only collapse WIDER
    (to the whole-command context); narrowing happens ONLY on a positively
    identified unique leg — dangerous here, or detect-positive in tier 2. Do
    NOT "fix" the tier-2 fallback back to whole-command as a regression: the
    two-tier basis is what keeps the read bind symmetric with the leg-bounded
    mint window for emergent-danger compounds.

    This is the substrate the read side uses to derive (op, target, bound_flags)
    from the destructive op ALONE — so a privileged flag on a benign NEIGHBOR leg
    cannot pollute bound_flags (the over-block) and a benign neighbor's PR number
    cannot cross-contaminate the target via _extract_pr_number's first-match-
    anywhere scan (the latent under-block). A privileged flag that modifies the
    destructive op is a token of its OWN simple-command (leg), so it stays bound;
    only a different statement's flag (past an operator boundary) is dropped.
    """
    dangerous = [
        leg.strip()
        for leg in _split_into_legs(command)
        if is_dangerous_command(leg.strip())
    ]
    return dangerous[0] if len(dangerous) == 1 else None


def _single_detectable_leg(command: str) -> str | None:
    """#1083 emergent-danger bind fallback: the UNIQUE leg that
    detect_command_operation_type classifies non-None, or None. Consulted ONLY
    when _single_destructive_leg found no individually-dangerous leg (the
    emergent case: whole-command danger from a cross-leg lookahead). Narrowing
    happens ONLY on positive unique identification — 0 or >=2 detectable legs
    fall through to the conservative whole-command context — so the abstain
    basis of _single_destructive_leg (ambiguity can only collapse WIDER, never
    narrower) is preserved: ambiguity still widens; only a positively unique
    op leg narrows. Binding from that leg is shell-faithful: a flag that
    modifies the executed op is a token of the op's OWN leg; a flag past an
    operator boundary belongs to a different statement.
    """
    detectable = [
        leg.strip()
        for leg in _split_into_legs(command)
        if detect_command_operation_type(leg.strip()) is not None
    ]
    return detectable[0] if len(detectable) == 1 else None


def is_compound_destructive_command(command: str) -> bool:
    """Detect an agent chaining MULTIPLE destructive operations into one command.

    Returns True iff the command joins >=2 DESTRUCTIVE legs with a shell operator
    (``&&``, ``||``, ``|&``, ``;``, ``&``, ``|``, newline), e.g.:

        gh pr close 5 -d && git branch -Df victim
        gh pr merge 100 && gh pr close 999 --delete-branch
        gh pr merge 5 && rm -rf /          # git/gh op chained with a plain `rm`

    This is the chaining analogue of the privileged-flag bind: both catch the agent
    doing MORE than the operator clicked — here, an ADDED destructive op the single
    approval did not cover. A leg counts as destructive if it is a recognized
    git/gh-destructive op OR its head command is a plain ``rm`` (the rm-EXCEPTION — see
    the module THREAT MODEL; rm is rm-specific by design and is NOT in
    is_dangerous_command, so a bare or pure-rm command is never gated). Honest-mistake
    model: a SINGLE destructive op plus a benign continuation / decoration /
    backgrounding is a faithful single-command click and MUST mint + execute — so
    `gh pr merge 5 && echo ok`, `gh pr merge 5 ; echo done`, `gh pr merge 5 &`,
    `gh pr merge 5 | tee log`, `gh pr merge 5 > out.log` are NOT compound-destructive
    (one destructive leg). Only >=2 destructive legs are refused (route to
    one-op-at-a-time approval).
    """
    # Legs come from the shared _split_into_legs SSOT (masked + FD-neutralized split
    # on _COMPOUND_OPS_RE, slices taken from `stripped`). A command with no operator
    # yields ONE leg, so the >=2 count below is False — identical to the prior
    # explicit no-operator short-circuit, without a separate code path that could
    # drift from the read-side leg isolation.
    legs = _split_into_legs(command)
    # >=2 DESTRUCTIVE legs → refuse. A leg is destructive via _leg_is_destructive: a
    # recognized git/gh-destructive op (so a non-canonical flag spelling like `-Df` in a
    # leg still counts) OR a plain-`rm` head leg (the documented rm-exception). A single
    # destructive leg + benign legs → NOT compound (the single op routes through its own
    # one-op approval as usual). This count is only consulted once is_dangerous_command is
    # already True (a git/gh op is present) at the read call site, so a bare `rm` or a
    # pure-rm chain — is_dangerous=False — never reaches it (the guard stays out of
    # pure-filesystem commands).
    return sum(1 for leg in legs if _leg_is_destructive(leg)) >= 2
