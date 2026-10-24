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

conservative-RECOGNITION (the design rule; documented so a future sweep does NOT
"discover" these forms and "harden" them into faithful-click over-blocks): recognition
targets the SINGLE destructive command an honest agent runs (the destructive op plus the
benign viewers/filters/redirects of benign-CONTINUATION above) and ERRS TOWARD LETTING
THROUGH — over-blocking a faithful click is WRONG BY DEFINITION (the INVARIANT above),
worse than missing a buried op. The ENTIRE flag-condition union arm
(_flag_condition_danger_op) needs a positional, quote-aware parse — the
close/branch-delete/force-push flag conditions AND the git-push remote-ref-delete
(`:ref` / `--delete` / `-d`) / mass-delete (`--mirror` / `--prune` / multi-ref)
extractors — so THIS FUNCTION is ANCHORED to the FIRST executable leg (the
_executable_prefix view), deriving both POSITIONALS and FLAGS from that ONE leg
(deriving flags from the WHOLE command while positionals came from the first leg let a
force/delete flag in a benign continuation leg mislabel a benign first-leg op — the
cross-leg flag leak).

ANY-LEG reach is the CALLERS' (`_PER_LEG_OPS`, below): they re-invoke this same arm on
each ISOLATED leg. That is NOT a match-anywhere scan — the distinction is the whole
point. A match-anywhere scan fires on a quoted `:ref` / `--mirror` mention in a benign
leg (a faithful-click over-block); a per-leg re-invocation runs the SAME positional,
quote-aware parse on a leg that is its own first leg, so a mention inside a benign leg
is a mention, not an op. Leg-locality is preserved BY CONSTRUCTION: the derivation is
per-leg on both axes, so the cross-leg flag leak cannot reappear through this route.
Consequently these forms DO gate in any leg position:
  - `cd /repo && git push origin --delete main`
  - `git fetch && git push --mirror origin`
  - `NOTE=x ; git push origin :main`
  - `cd /repo && git branch -Df temp`   (cluster force-delete)
  - `cd /repo && gh pr close 5 -d`      (short `-d` close)
The earlier design accepted these as UNGATED, on the premise that reaching them required
a match-anywhere scan. That premise was false — the per-leg predicate reaches them with
no match-anywhere behavior — and leaving them ungated was a good-faith-reachable
under-block, so they were closed. What must NOT be "fixed" is the literal arms' word
boundary: granting delete tokens match-anywhere coverage IS the over-block this design
rule guards, and it remains guarded. httpie
(`http` / `https` CLI) is likewise WHOLLY ungated by design — ref-mutation, merge, AND
protection-mutation — because the MINT classifier covers gh-api / curl / wget only; ANY
httpie read-floor arm re-creates a gated-but-unmintable over-block. Ungated keeps read == mint.
NB the first-leg anchoring is a property of `_flag_condition_danger_op` ITSELF, not of the
guard's leg coverage. TWO independent mechanisms deliver ANY-LEG reach, and both are
per-leg rather than match-anywhere: the LITERAL danger arms (the DANGEROUS_PATTERNS bank
+ the per-leg literal-arm tuples: force-push, branch-delete, close, API ref/protection)
match in ANY leg position (`cd /repo && git push --force origin main` is caught); and the
accurate refspec-DST predicate `_flag_condition_danger_op` is additionally invoked PER-LEG
by its callers (detect_command_operation_type + _stripped_surface_danger), filtered to
`_PER_LEG_OPS` — so `cd /repo && git push origin main` is caught. That filter carries ALL
SIX union-arm classes, so the delete/mirror/:ref forms gate in any leg too. The two
mechanisms OVERLAP on some forms (e.g. `cd /repo && git branch -D temp` is covered by
both); when proving either one is load-bearing, isolate it — an assertion that exercises
the whole pipeline passes while one mechanism is silently dead. When an over-block of a
faithful click is found, the fix WIDENS the mint, never narrows detection into a new
under-block.
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

import ast
import functools
import glob
import json
import os
import re
import shlex
import time
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

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
# Tight variant for PR-number extraction; used only by _GH_PR_NUMBER_RE.
#
# THE OPTIONAL VALUE MUST NOT START WITH `-`, AND THAT IS THE WHOLE POINT.
# Requiring a leading `-` per FLAG does not make this linear on its own — an
# earlier comment here claimed it did ("already linear ... fails fast"), and
# that claim is why an exponential shipped: it was written down, so nobody
# re-measured. MEASURED with the old `(?:\s+\S+)?` value arm, on consecutive
# valueless dash-flags with the match failing: 18 flags 1.0 ms, 22 flags 6.6 ms,
# 26 flags 45 ms — roughly 2.5x per two added flags, extrapolating to the 600 s
# PreToolUse ceiling at ~46 flags. A hook that exceeds that ceiling is KILLED
# AND THE TOOL CALL PROCEEDS, so a hang here is a silent guard BYPASS, not a
# slow refusal.
#
# The ambiguity was structural: with `\S+` as the value, a `-x` token could be
# EITHER the previous flag's value OR a flag in its own right, so every token
# doubled the partitions the engine had to try. `[^-\s]\S*` makes that
# unrepresentable — a dash-initial token can only ever be a flag, a
# non-dash-initial token can only ever be a value, and the partition is unique.
#
# THE MATCHED LANGUAGE IS NOT UNCHANGED, AND THIS ARM IS THEREFORE LOAD-BEARING
# FOR CORRECTNESS, NOT ONLY FOR BACKTRACKING. A value that genuinely starts with
# `-` (`--subject -x 42`) is still consumed — by the NEXT iteration as a flag
# rather than by this one as a value — but the flag run then STOPS IN A
# DIFFERENT PLACE, which moves the capture and the match end together. The broad
# form reads a NUMERIC FLAG ARGUMENT as the pull-request number:
#
#     gh pr merge --squash -t 2024 42        this arm -> 42   broad -> 2024
#     gh pr merge --squash --repo 2024 42    this arm -> 42   broad -> 2024
#
# On a control that binds an approval to a specific pull request, that is a
# TARGET-IDENTIFICATION defect: THE APPROVAL BINDS TO A PULL REQUEST OTHER THAN
# THE ONE THAT MERGES. If you are reading this because you want to simplify the
# pattern, that sentence is the cost — not a slower match, a merge the user did
# not authorise.
#
# THE EXAMPLES ARE SHORT-FORM AND `--repo` ON PURPOSE, BECAUSE THE LONG FORMS DO
# NOT DEMONSTRATE IT. `_extract_pr_number` re-checks the token preceding the
# captured digit against `_GH_PR_VALUE_TAKING_FLAGS` and ABSTAINS on a hit, so
# the caller falls through to `_extract_merge_target` and recovers the right
# target. Every member of that frozenset is long-form, so a broadened arm is
# silently repaired for `--subject`/`--body-file`/`--body` and NOT repaired for:
#   * SHORT ALIASES (-t -b -F -c -A -R). The re-check is `(--[\w-]+)$` — it can
#     only see a LONG form, so a short alias never reaches the membership test.
#     ADDING ONE TO THE FROZENSET WOULD CHANGE NOTHING; the regex would still
#     not match it. This is the half that governs most of the misbinding forms.
#   * `--repo`. Long-form and visible to the re-check, but genuinely absent from
#     the frozenset. This half IS fixable by extending it.
# The same seven flags are enumerated as `_INERT_HELP_EXTRA_VALUE_FLAGS` below,
# for an unrelated purpose (inert-help recognition), and that block already
# NAMES this misbind and scopes it out on purpose. Read as a POINTER, not a
# dependency: the two sets coincide because both derive from "value-taking flags
# the long-form SSOT does not cover", but they can drift — move `--repo` into
# the SSOT and it stops misbinding while plausibly staying in the inert set.
# An earlier version of this paragraph illustrated the defect with `--subject`
# and `--body-file` — both in the frozenset, so NEITHER example misbound, and a
# maintainer who checked them would have concluded the warning overclaimed and
# broadened the arm anyway. A true warning with a demonstration that refutes it
# on inspection fails in the same direction as a false one.
#
# THE TRIGGER IS ORDINARY RATHER THAN ADVERSARIAL — a `-t` subject that is a
# year, a `-F` body file named `1`. Under this guard's honest-mistake threat
# model that is the reachable case, not an exotic one.
#
# WHAT IS CLAIMED, AND AT WHICH LAYER. The divergence is a property of the
# EXTRACTED TARGET, not merely of the regex capture, and the distinction is
# what the earlier version got wrong: it counted regex-level divergences and
# wrote them under a sentence about what the guard binds. Structural claims,
# pinned by tests rather than asserted here — see the flag-token and
# dash-initial classes in tests/test_merge_guard_pre.py and
# tests/test_merge_guard_perf.py:
#   * a divergent form needs TWO leading dash-tokens; no single-flag shape
#     reaches it, which is why hand-picked corpora kept missing it —
#     `test_a_single_leading_dash_token_never_diverges`;
#   * the split is CLEAN BY FLAG FORM — long-forms in the frozenset neutralise,
#     short aliases and `--repo` misbind, with no crossover, on BOTH the merge
#     and close verbs — `TestExtractPrNumberFlagFormAsymmetry`.
# Deliberately NOT recorded here: corpus sizes and divergence counts. Several
# such figures stood in this paragraph and none was reproducible, because no
# enumeration harness was ever committed. A count that cannot be re-derived is
# the artifact that has failed repeatedly in this exact spot — including, once,
# a throughput figure sitting five lines below this very sentence.
#
# WHAT THE TWO BULLETS DO NOT COVER, because both were derived from the flag
# lists in THIS FILE rather than from `gh` itself: a value-taking flag that is
# real but absent from BOTH `_GH_PR_VALUE_TAKING_FLAGS` and
# `_INERT_HELP_EXTRA_VALUE_FLAGS` would be missing from any corpus built the way
# these were, and no amount of agreement between such corpora would reveal it.
# That is not hypothetical: `--repo` IS such a flag, and it was caught only
# because the second frozenset happened to list it. So read the bullets as
# holding FOR THE FLAGS THIS FILE ENUMERATES — deriving the alphabet from
# `gh pr merge --help` is the independent check, and it has not been done.
#
# NOT enumerated, so not claimed either way: bundled short clusters, and
# `--flag=value` spellings.
#
# Linearity after the repair is pinned by
# `TestGhFlagTokenAmbiguityStaysRemoved` in tests/test_merge_guard_perf.py,
# which holds 36 consecutive valueless flags under a 1 s ceiling against a
# pre-fix cost of ~4 s at the same width.
_GH_FLAG_TOKENS   = r"(?:-\S*(?:\s+[^-\s]\S*)?\s+)*"
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
# The short cluster arm is TOKEN-START anchored `(?<!\S)` so it can only match a `-`
# beginning a word: without it, `-[a-ln-zA-Z]*m` mis-matches MID-FLAG inside a longer
# option (`-com` in `--committer`, `-adm` in `--admin`, `-am` in `--amend`), which lets
# carrier-7e's git-merge span mangle a `git log --committer '…merge…'` surface and leave
# the danger visible (an over-block). Genuine token-start clusters (`-m`, `-am`, `-cm`)
# are whitespace-preceded so the anchor still admits them (no strip regression).
_MSG_FLAG_ANCHOR = r"((?:--m(?:e(?:s(?:s(?:a(?:g(?:e)?)?)?)?)?)?|(?<!\S)-[a-ln-zA-Z]*m)\s*)"

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
# A gh-api merge endpoint, as a whole PATH segment: `merge` (a pull request), `merges` (a
# branch into a branch) or `merge-upstream` (upstream into a fork's branch).
_GH_API_MERGE_PATH = r".*/(?:merges?|merge-upstream)(?![\w-])"

_API_LITERAL_ARMS = (
    # API-based merge bypasses (require mutating HTTP method to avoid blocking reads).
    # The gh-api arms read `merge` only as an endpoint PATH segment (`/pulls/<n>/merge`,
    # `/merges`, `/merge-upstream`), never inside a field name: `-f delete_branch_on_merge=true` is a repo
    # settings change, not a merge.
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X|--method)\s+(?:PUT|PATCH|POST)\b)" + _GH_API_MERGE_PATH, re.IGNORECASE),
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
    re.compile(_GH_API_PREFIX + r"(?!.*(?:-X|--method)\s+GET\b)(?=.*(?:-f|-F|--field|--raw-field|--input)\s)" + _GH_API_MERGE_PATH, re.IGNORECASE),
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
    # GraphQL merge mutations are read by _graphql_merge_mutation, beside these arms.
    # Known API detection gaps (defense-in-depth, not a security boundary):
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
    # TWO-PASS (view-first, raw fallback): recognition's PRIMARY surface for the
    # gh-pr PROSE arms is the executed-surface view — quoted data (a --comment/-m
    # message) is masked, so an op keyword living in prose no longer classifies
    # (the cross-auth cure: `gh pr close 5 --comment '…gh pr merge 5…'`
    # classifies close, not merge, so mint and read bind the SAME faithful
    # (op, target)). The raw fallback pass is arm-for-arm equivalent to the
    # pre-view classifier, so every currently-recognized spelling keeps its
    # non-None result BY CONSTRUCTION — load-bearing for the module INVARIANT:
    # a wrapped faithful click (`bash -c 'gh pr merge 5'`) classifies None on
    # the view (its payload is masked) and the read side REFUSES on a None op,
    # so a view-only detect would permanently block it.
    #
    # ONLY the gh-pr merge/close arms consult the view. Every push/branch arm
    # runs on RAW in BOTH passes: those are POSITIONAL / flag-presence
    # classifiers, and masking a cosmetically-quoted EXECUTING token (a quoted
    # remote name — `git push 'origin' --delete main`) turns it into a
    # whitespace gap that a positional regex binds ACROSS, downgrading the op
    # (remote-ref-delete → push-to-main) and laundering a benign push-to-main
    # token into a delete authorization. The gh-pr prose arms are presence
    # detectors with no positional mask-gap bind, so they carry no analogue.
    view = _executed_surface_view(command)
    legs = _split_into_legs(command)
    op = (
        _detect_op_pass(command, view, legs)      # view pass (gh-pr prose arms on view)
        or _detect_op_pass(command, command, legs)  # raw fallback == pre-view classifier
    )
    if op is not None:
        return op
    # PER-LEG arms: the union arm is FIRST-LEG-anchored, so any op it recognizes in a
    # non-first leg (`cd /repo && git push origin main`, `cd /repo && git push origin
    # --delete feature`) needs a per-leg call. CALLER-LEVEL (not inside _detect_op_pass):
    # reached ONLY after BOTH passes return None, so no command that already classifies
    # changes verdict (the api-merge additive precedent), and the raw-fallback gh-pr arms
    # still run before this (so
    # `bash -c 'gh pr merge 5' && git push origin main` stays merge, not push-to-main).
    for _leg in legs:
        _lop = _flag_condition_danger_op(_leg)
        if _lop in _PER_LEG_OPS:
            return _lop
    mutation = _graphql_input_mutation(command)
    if mutation is not None:
        return "branch-merge" if mutation in _BRANCH_MERGE_MUTATIONS else "merge"
    return None


def _detect_op_pass(
    command: str,
    surface: str,
    raw_legs: "list[str]",
) -> str | None:
    """One precedence-ordered classification pass, parameterized by surface.

    - `surface`: the string the gh-pr PROSE arms (merge/close) match. View pass:
      the executed-surface view; fallback pass: the raw command. These are the
      ONLY view-consulting arms (presence detectors — no positional mask-gap).
    - `command` / `raw_legs` are ALWAYS the raw inputs — every other arm
      consumes them in BOTH passes and MUST NOT move to the view:
        * force-push / push-to-main (both arms) / branch-delete: positional or
          flag-presence classifiers on git commands — masking a cosmetically-
          quoted EXECUTING token (quoted remote) lets the positional arm bind
          across the gap and DOWNGRADE the op (the quoted-remote-delete
          laundering), or hides a quoted flag from a higher-precedence arm.
          RAW == the certified base behavior for these classes.
        * gh-api/curl/wget git-refs + branches/protection arms: a quoted
          `gh api 'repos/o/r/git/refs/…' -X DELETE` is a faithful MINTING
          spelling; the view blanks the quoted URL, so a view-side match would
          make it gated-but-unmintable (a cardinal over-block).
        * _api_merge_leg_endpoint: recognition⟺extractability coupling — the
          leg classifies merge IFF its endpoint-position PR is extractable from
          the SAME surface; masking would decouple them.
        * _flag_condition_danger_op: already quote-aware via shlex and embeds
          the remote-ref/mass-delete extractors (recognition⟺mintability); a
          masked input would break quoted-refspec (`':main'`) extraction.
    Precedence order is byte-identical to the pre-view classifier."""
    # Order matters: gh pr close --delete-branch is BOTH a close and a
    # branch-delete operation; the AskUserQuestion-side classifier
    # (extract_context) tags it as "close" in priority order, so match
    # the same precedence here for write/read symmetry.
    if _GH_PR_MERGE_RE.search(surface):
        return "merge"
    if _GH_PR_CLOSE_RE.search(surface):
        # gh pr close --delete-branch is a close-type operation per the
        # write-side classifier. Branch-delete-via-pr-close is folded into
        # the close class on both sides for symmetric authorization.
        return "close"
    # force-push: git push ... --force (excludes --force-with-lease — carved out
    # of the force-push arms ONLY; the push-to-main arm still gates lease pushes
    # to a default branch). Matched PER-LEG over the shared _FORCE_PUSH_LITERAL_ARMS
    # SSOT (#1082) — the same arm list the read floor consumes, over the same leg
    # substrate (_split_into_legs), so a force-class flag in a benign continuation
    # leg no longer classifies the first-leg push as force-push and read==mint
    # holds by construction on this class.
    for _leg in raw_legs:
        if any(arm.search(_leg) for arm in _FORCE_PUSH_LITERAL_ARMS):
            return "force-push"
    # Direct push to a default branch (main/master) — plain OR --force-with-lease —
    # is a review-bypass, a DISTINCT op from force-push. It is now recognized by the
    # UNIFIED refspec-DST predicate in the shared `_flag_condition_danger_op` push-to-main
    # arm (#1195 OBS-E), reached via the union-arm fallback at the END of this function —
    # so the former inline literals here (a `HEAD:(?:main|master)` arm + a bounded-flag-walk
    # `(?:main|master)(?!:)` arm) were REMOVED. The unified predicate is accurate BOTH ways
    # the literals were not (it excludes main-release/main.foo prefix over-blocks and
    # catches feature:main / refs/heads/main under-blocks), and being the single shared arm
    # both detect and the read floor call it is mint==read by construction. The --force/-f
    # checks ABOVE still run first (a forced push to main returns force-push).
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
    # case-SENSITIVE pulls/<N>/merge path. DELETE excluded; the implicit-POST spelling (a gh api
    # body field flag and no GET) is a POST too, so it is recognized the same way.
    for _leg in raw_legs:
        if _api_merge_leg_endpoint(_leg) is not None:
            return "merge"
    for _leg in raw_legs:
        mutation = _graphql_merge_mutation(_leg)
        if mutation is not None:
            return "branch-merge" if mutation in _BRANCH_MERGE_MUTATIONS else "merge"
        if _gh_api_branch_merge_leg(_leg) or _update_branch_target(_leg) is not None:
            return "branch-merge"
    # branch-delete: git branch -D / --delete --force / --force --delete. Matched
    # PER-LEG over the shared _BRANCH_DELETE_LITERAL_ARMS SSOT (#1094) — the same
    # arm tuple the read floor consumes, over the same leg substrate
    # (_split_into_legs), so a stray force-delete token in a benign continuation
    # leg no longer classifies the compound as branch-delete, and read==mint
    # holds by construction on this class (a command is gated via these arms iff
    # some stripped leg matches iff detect classifies branch-delete here).
    # Clustered spellings (-Df / -fD / --delete -f) fall through to the union-arm
    # fallback below, same as the read floor.
    for _leg in raw_legs:
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

# #1203 C1b — value-taking gh pr merge/close flags the inert-help recognizer must skip
# BEYOND the shared _GH_PR_VALUE_TAKING_FLAGS long-form SSOT, so a `-h`/`--help` sitting
# in one of their VALUE positions (separate form `-t -h`, `-R -h`, `--repo -h`) is not
# misread as a help request and does not un-gate a REAL merge/close (the auditor's
# confirmed under-block). Two groups:
#   • SHORT aliases of the shared long value flags: -t(--subject) -b(--body)
#     -F(--body-file) -A(--author-email) -c(--comment).
#   • --repo AND its short -R — a value-taking flag on merge AND close (test-engineer
#     #68, live-verified) that is ABSENT from the shared long SSOT.
# KEPT SEPARATE from _GH_PR_VALUE_TAKING_FLAGS ON PURPOSE: that SSOT is also consumed by
# _extract_pr_number + the merge shadow-guard, so widening it would change target
# extraction (the SEPARATE short/-repo pr_number MISBIND is escalated on its own, not
# folded here). This set touches ONLY the inert recognizer.
# ONLY genuinely value-taking flags belong here — a BOOLEAN (e.g. -d/--delete-branch,
# -s/--squash, -r/--rebase, -m/--merge) would skip a real following `-h` and RE-INTRODUCE
# an over-block; those are correctly EXCLUDED. The GLUED form (`-tVALUE` / `-t-h` /
# `--repo=-h` / `-R=-h`) needs no entry — one token, not literally `-h`/`--help`, so it
# neither skips nor matches (stays gated).
_INERT_HELP_EXTRA_VALUE_FLAGS = frozenset(
    {"-t", "-b", "-F", "-A", "-c", "-R", "--repo"}
)


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


# A `gh pr close` PR URL: host + owner/repo + the /pull/<N> segment. Anchored on
# the LITERAL `/pull/` path element, so digits inside owner/repo (`o/r-123`) are
# never mistaken for the PR number; `(\d+)(?![\w-])` stops exactly at the PR
# number, so a trailing path (`/pull/5/files`), query (`?diff=split`), or
# fragment (`#c-9`) cannot extend or replace it. The host char class accepts a
# PORT (`ghe.corp:8443`) and USERINFO (`user@github.com`) so a faithful GHE-with-
# port or userinfo url MINTS — gh RUNS both (empirically confirmed), so leaving
# them target=None was a gated-but-unmintable over-block. The widen is HOST-
# SEGMENT ONLY: the owner/repo group and the `/pull/(\d+)(?![\w-])` anchor are
# unchanged, so the PR number still resolves exactly (no anchor drift). The host
# is captured VERBATIM into the minted identity (NOT normalized): distinct
# spellings mint distinct identities (`user@github.com` != `github.com`,
# `ghe.corp:8443` != `ghe.corp`), so a token binds ONLY its own exact command and
# can NEVER cross-authorize a different host/PR (mint==read by construction; over-
# block-safe, never a launder — a deceptive `github.com@evil.com` mints its own
# verbatim id and authorizes only that exact typed command). STILL UNMATCHED: a
# BRACKETED IPv6-literal host (`https://[::1]/o/r/pull/9`) — `[`/`]` are outside
# the class — a non-realistic faithful form that stays a safe-direction over-
# block; widen to include `[]` only if a real gh IPv6 form ever appears.
_GH_PULL_URL_RE = re.compile(
    r"https?://([\w.@:-]+)/([\w.-]+/[\w.-]+)/pull/(\d+)(?![\w-])"
)

# The COMPLETE set of value-taking flags on `gh pr close` (verified against
# `gh pr close --help`): `-R/--repo` and `-c/--comment`. `-d/--delete-branch` is
# boolean. This set is used to strip a flag's VALUE from the positional scan so a
# repo (`--repo o/r`) or comment (`-c "msg"`) value is never mistaken for the
# {number|url|branch} target.
#
# WHY gh IS SOLVABLE WHERE curl/wget IS NOT (the #1098 WON'T-FIX contrast): gh's
# value-flag vocabulary for a given subcommand is BOUNDED and documented (two
# flags here), so this set can be COMPLETE for current gh — every faithful form
# is handled, zero over-block today. curl/wget's value-flag vocabulary is
# unbounded and version-growing, which is why a sound extractor there is
# impossible and #1098 closed WON'T-FIX. Bounded => completable is the whole
# reason gh-close has a real fix.
#
# BEHAVIOR ON AN UNLISTED VALUE-FLAG — two sub-cases, stated honestly (NOT
# "never a mis-bind"):
#  (a) the flag's value is an EXTRA token alongside a real target
#      (`gh pr close feature --newflag x`) -> TWO positionals -> ABSTAIN
#      (over-block, the SAFE/VISIBLE direction; fixable by listing the flag).
#  (b) the flag's value is the SOLE positional-shaped token
#      (`gh pr close --newflag 5`, NO real target) -> that value is bound as the
#      target. BUT such a command has no valid {number|url|branch} positional, so
#      gh ITSELF REJECTS it -> it is NON-FAITHFUL. Minting from it is a
#      confused-deputy: an adversary must get the user to APPROVE a command gh
#      won't run, then reuse the token on a real one. ADVERSARIAL-ONLY, tolerated
#      under the good-faith model (a good-faith user never approves a command that
#      cannot execute). This is a DOCUMENTED RESIDUAL, not closed here — see
#      test_unknown_value_flag_on_nonfaithful_close_is_adversarial_only.
# `--repo`/`-c/--comment` are LISTED, so the security-reported FAITHFUL mis-bind
# (`gh pr close --repo 5/6 feature` shadowing the branch with the repo digit) is
# fully closed. curl/wget contrast (#1098): gh's per-subcommand value-flag set is
# bounded/documented so the FAITHFUL forms are completely covered; curl/wget's is
# unbounded, so no sound extractor exists there.
_GH_CLOSE_VALUE_LONG = frozenset({"--repo", "--comment"})
_GH_CLOSE_VALUE_SHORT = frozenset("Rc")


def _gh_close_positionals(tokens: "list[str]") -> "list[str] | None":
    """The positional args of a ``gh pr close`` token list, with value-taking
    flags AND their values removed (cobra semantics: clustered shorts, ``=``-joined
    and attached values all handled). Returns the positionals after ``close``, or
    None if ``close`` is absent. A repo/comment VALUE can never survive as a
    positional, so it can never be mis-bound as the {number|url|branch} target."""
    try:
        i = tokens.index("close") + 1
    except ValueError:
        return None
    positionals: "list[str]" = []
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.startswith("--"):
            flag, has_eq, _v = tok.partition("=")
            # `--repo value` / `--comment value` consume the NEXT token; the
            # `=`-joined and boolean/unknown long forms are self-contained.
            i += 2 if (flag in _GH_CLOSE_VALUE_LONG and not has_eq) else 1
            continue
        if tok.startswith("-") and tok != "-":
            # Short cluster (cobra): a value-taking short consumes the REST of the
            # cluster as its attached value, or — if it is the last char — the NEXT
            # token. Booleans before it (`-dR value` = -d + -R value) don't matter.
            body = tok[1:]
            consumes_next = False
            for j, ch in enumerate(body):
                if ch in _GH_CLOSE_VALUE_SHORT:
                    consumes_next = j == len(body) - 1   # `-R value`; else `-Rvalue`
                    break
            i += 2 if consumes_next else 1
            continue
        positionals.append(tok)
        i += 1
    return positionals


def _classify_pr_target(target: str) -> str | None:
    """Classify a single ALREADY-QUOTE-STRIPPED gh-pr positional into one of FOUR
    disjoint identity namespaces — the SHARED SSOT (#1203 M1) for BOTH the close and
    the merge target identity, so the two can never diverge:

    - empty / degenerate         -> None (abstain, never a garbage id)
    - bare digits                -> the digits (repo-IMPLICIT PR number, gh's number
                                    precedence)
    - a github/enterprise PR URL -> ``url:<host>/<owner>/<repo>#<N>`` (HOST- and
                                    repo-QUALIFIED, so a cross-host/cross-repo url can
                                    never set-equal a bare number or a same-number url
                                    in a different repo)
    - a ``://``-bearing token that is NOT a recognized PR URL -> None (abstain, never a
                                    garbage ``branch:`` id)
    - anything else              -> ``branch:<name>`` (repo-IMPLICIT name-only;
                                    cross-repo distinction is carried SEPARATELY by
                                    ``--repo`` in bound_flags, the a2 axis)

    Extracted VERBATIM from _extract_close_target's classification tail so close's
    output stays byte-identical; _extract_merge_target feeds it a MERGE-value-flag-aware
    positional (its own walk) — the CLASSIFICATION is shared, the positional walk is
    op-specific. A branch literally named ``url:x`` classifies ``branch:url:x`` (no
    ``://`` -> the branch namespace), never colliding with a real ``url:`` identity —
    the namespaces are injective by construction."""
    if not target:
        return None  # empty/degenerate positional -> abstain, never a garbage id
    if target.isdigit():
        return target  # bare PR number — gh's number precedence, repo-implicit
    url = _GH_PULL_URL_RE.search(target)
    if url is not None:
        return "url:" + url.group(1) + "/" + url.group(2) + "#" + url.group(3)
    if "://" in target:
        return None  # unrecognized URL form -> abstain, never a garbage branch id
    return "branch:" + target


def _extract_close_target(command: str) -> str | None:
    """The close-target identity for a faithful ``gh pr close`` command, across
    all three positional forms gh accepts (``{<number> | <url> | <branch>}``).

    Returns one of FOUR disjoint identity namespaces (so no spelling can ever
    set-equal another):

    - bare PR number -> the digits (``"5"``). Repo-IMPLICIT, exactly as the
      pre-existing number-close identity already is.
    - github/enterprise PR URL -> ``"url:<host>/<owner>/<repo>#<N>"``. HOST- and
      repo-QUALIFIED on purpose: a URL names an explicit repo, so binding only
      the number would let it set-equal a bare ``"5"`` and authorize closing PR
      #5 in a DIFFERENT repo (cross-repo target confusion). The number is parsed
      from the ``/pull/<N>`` segment gh itself resolves, so the identity IS the
      PR the command closes.
    - single branch positional -> ``"branch:<name>"``. Name-only (repo-implicit
      like the number form — a bare branch carries no repo). Branch names may
      contain ``/`` (``feature/foo``), so slashes are allowed; a token carrying
      ``://`` is an UNRECOGNIZED url form (not a github ``/pull/`` URL) -> abstain
      rather than mint a garbage ``branch:`` identity.
    - none of the above (incl. the NO-positional form ``gh pr close -d``, which
      gh itself refuses) -> ``None`` (abstain; the read floor still gates, but a
      command gh will not run has no faithful click to over-block).

    LEG-LOCAL: everything is derived from ``_executable_prefix(command)`` (the
    first executable leg), so a URL/branch mentioned in a benign continuation leg
    is never picked up (no cross-leg identity injection). Dash-prefixed tokens
    are dropped from the positional scan, so a flag can never be bound as the
    target. Numeric precedence matches gh's own: a bare digit is always the PR
    number, so a branch literally named ``5`` is indistinguishable from PR #5 to
    BOTH gh and this guard (documented, not a collision — the two resolve to the
    same command).

    SINGLE VALUE-FLAG SSOT (structural — do NOT reintroduce a second list): ALL
    three forms (number, url, branch) are classified from the ONE positional that
    ``_gh_close_positionals`` returns, which strips value-flag values via the
    single ``_GH_CLOSE_VALUE_*`` set. The number path deliberately does NOT call
    ``_extract_pr_number`` — that function's value-flag handling is (a) long-form
    only (blind to ``-R``) and (b) a DIFFERENT, incomplete flag list, so routing
    close numbers through it re-created the two-lists-diverge mis-bind: on
    ``gh pr close --repo 5/6 feature`` / ``-R 5/6 feature`` its regex backtracks
    to grab the repo's leading digit as the PR (shadowing the branch), which
    LAUNDERED an approved branch-close into a different-PR close. Deriving every
    close form from the one value-flag-complete positional walk makes that
    divergence impossible by construction. (merge still uses ``_extract_pr_number``
    — its value-flags differ; this SSOT is close-scoped.)"""
    prefix = _executable_prefix(command)
    if prefix is None:
        return None  # unbalanced quote / procsub -> abstain (existing safe posture)
    tokens = _shell_tokenize(prefix)
    if tokens is None:
        return None  # unparseable -> abstain
    positionals = _gh_close_positionals(tokens)
    # Exactly ONE positional is a single-target close. 0 (the no-arg form gh
    # refuses) or >=2 (ambiguous / an unlisted value-flag's value survived) ->
    # abstain; the >=2 case is the over-block-safe fail direction.
    if positionals is None or len(positionals) != 1:
        return None
    target = _strip_surrounding_quotes(positionals[0])
    # #1203 M1 — classify via the SHARED _classify_pr_target SSOT (extracted VERBATIM
    # from here, so close's output is byte-identical; merge reuses the SAME classifier
    # over its OWN merge-value-flag-aware positional walk). Value-flag values are
    # already stripped by _gh_close_positionals, so the digit/url/branch here IS the
    # target, never a --repo/--comment value.
    return _classify_pr_target(target)


# #1203 M1 — MERGE positional-walk value-flags: the merge value-taking flags whose value
# token must be stripped so the branch/url TARGET positional is isolated — the SAME set
# C1b established (the long value SSOT + --repo + the short aliases), NOT close's
# {--repo,--comment}. DERIVED from the C1b sets so there is ONE value-flag SSOT: a merge
# value-flag added for inert-help recognition is automatically honored by the target walk
# too (the divergent-copies bug class this arc kept hitting cannot recur).
_GH_MERGE_VALUE_LONG = _GH_PR_VALUE_TAKING_FLAGS | {"--repo"}
_GH_MERGE_VALUE_SHORT = frozenset(
    f[1] for f in _INERT_HELP_EXTRA_VALUE_FLAGS if len(f) == 2 and f[0] == "-"
)


def _gh_merge_positionals(tokens: "list[str]") -> "list[str] | None":
    """The positional args of a ``gh pr merge`` token list, with MERGE value-taking flags
    AND their values removed (mirrors _gh_close_positionals's cobra walk, but over the
    MERGE value-flag set — NOT close's {--repo,--comment}: a merge form carries
    --subject/--body/-t/… so close's walk would mis-read a value as the target, reviving
    M1 one layer down). Returns the positionals after ``merge``, or None if ``merge`` is
    absent. A value-flag VALUE can never survive as a positional, so it can never be
    mis-bound as the {number|url|branch} target."""
    try:
        i = tokens.index("merge") + 1
    except ValueError:
        return None
    positionals: "list[str]" = []
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.startswith("--"):
            flag, has_eq, _v = tok.partition("=")
            i += 2 if (flag in _GH_MERGE_VALUE_LONG and not has_eq) else 1
            continue
        if tok.startswith("-") and tok != "-":
            body = tok[1:]
            consumes_next = False
            for j, ch in enumerate(body):
                if ch in _GH_MERGE_VALUE_SHORT:
                    consumes_next = j == len(body) - 1   # `-R value`; else `-Rvalue`
                    break
            i += 2 if consumes_next else 1
            continue
        positionals.append(tok)
        i += 1
    return positionals


def _extract_merge_target(command: str) -> str | None:
    """The EXPLICIT merge-target identity for a faithful ``gh pr merge <branch>`` /
    ``<url>`` (or ``<number>``, though _extract_pr_number owns that first), or None
    (#1203 M1). Closes the cross-auth where every NON-numeric merge collapsed into the
    target-blind merge_implicit sentinel: it isolates the target positional via the
    MERGE-value-flag-aware _gh_merge_positionals (so ``--subject foo`` is NOT read as the
    target), requires EXACTLY ONE positional (0 = truly-bare -> the caller mints
    merge_implicit; >=2 = ambiguous -> abstain, gated-but-unmintable, the cross-auth-SAFE
    direction), then classifies it via the SHARED _classify_pr_target (namespaces
    IDENTICAL to close: number / url:<host>/<owner>/<repo>#N / branch:<name>). Abstains on
    unbalanced quote / procsub / no merge token — the existing safe posture; mint==read
    holds because BOTH hook arms derive via extract_command_context."""
    prefix = _executable_prefix(command)
    if prefix is None:
        return None
    tokens = _shell_tokenize(prefix)
    if tokens is None:
        return None
    positionals = _gh_merge_positionals(tokens)
    if positionals is None or len(positionals) != 1:
        return None
    return _classify_pr_target(_strip_surrounding_quotes(positionals[0]))


def _is_bare_cli_merge(command: str) -> bool:
    """True iff ``command`` is a TRULY-BARE CLI ``gh pr merge`` — the CLI merge shape
    (_GH_PR_MERGE_RE on the quote-masked view, which excludes an API merge that always
    carries an explicit endpoint PR) with ZERO real positional target (flags-only). This
    is the ONLY form that mints the target-blind merge_implicit sentinel (#1203 M1: a
    branch/url positional binds an explicit target via _extract_merge_target instead; a
    >=2 ambiguous count abstains). The 0-positional test uses the SAME _gh_merge_positionals
    walk _extract_merge_target uses, so bare-vs-explicit is ONE boundary (no double-count)."""
    if not _GH_PR_MERGE_RE.search(_executed_surface_view(command)):
        return False
    prefix = _executable_prefix(command)
    if prefix is None:
        return False
    tokens = _shell_tokenize(prefix)
    if tokens is None:
        return False
    return _gh_merge_positionals(tokens) == []


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
    if not (re.search(r"\b(?:PUT|PATCH|POST)\b", leg, re.IGNORECASE) or _gh_api_implicit_post(leg)):
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


def _gh_api_implicit_post(leg: str) -> bool:
    """Whether a gh api leg is an implicit POST: a body field flag and no GET method,
    the same condition as the implicit-POST read arm."""
    return bool(
        re.search(r"(?:^|\s)(?:-f|-F|--field|--raw-field|--input)\s", leg)
        and not re.search(r"(?:-X|--method)\s+GET\b", leg, re.IGNORECASE)
    )


# A gh api call that merges a branch into another (`POST repos/O/R/merges`, or
# `POST repos/O/R/merge-upstream` from the upstream repository): the endpoint as the last
# path segment, sent with a mutating method, explicit or implicit.
_GH_API_MERGES_ENDPOINT_RE = re.compile(r"/(?:merges|merge-upstream)(?=$|[\s'\"?])")


def _gh_api_branch_merge_leg(leg: str) -> bool:
    """Whether a leg is a gh api call to the branch-merge endpoint."""
    return bool(
        re.search(_GH_API_PREFIX, leg, re.IGNORECASE)
        and _GH_API_MERGES_ENDPOINT_RE.search(leg)
        and (
            re.search(r"(?:-X|--method)\s+(?:PUT|PATCH|POST)\b", leg, re.IGNORECASE)
            or _gh_api_implicit_post(leg)
        )
    )


# GraphQL mutations that merge: a gh api graphql request whose query calls one of them
# merges a pull request (directly, by auto-merge or through the merge queue) or merges into
# a branch. The query value is the request, so it is read.
_GRAPHQL_MERGE_MUTATION_RE = re.compile(
    r"\b(mergePullRequest|enablePullRequestAutoMerge|enqueuePullRequest|mergeBranch"
    r"|updatePullRequestBranch)\s*\("
)
_GH_API_GRAPHQL_RE = re.compile(_GH_API_PREFIX + r".*\sgraphql(?![\w./-])")
_GRAPHQL_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')


def _graphql_merge_mutation(leg: str) -> str | None:
    """The merge mutation a gh api graphql leg's query calls, or None. A mutation name
    inside a GraphQL string literal (a comment body) is data."""
    if not _GH_API_GRAPHQL_RE.search(leg):
        return None
    tokens = _shell_tokenize(leg) or [leg]
    for tok in tokens:
        m = _GRAPHQL_MERGE_MUTATION_RE.search(_GRAPHQL_STRING_RE.sub('""', tok))
        if m:
            return m.group(1)
    return None


# `gh api graphql --input -` (or `--input <(…)`): the request body comes from stdin or a
# process substitution, which the guard reads when the command writes it literally.
_GH_API_GRAPHQL_INPUT_RE = re.compile(
    _GH_API_PREFIX + r"[^\n;&|]*\sgraphql(?![\w./-])[^\n;&|]*?\s--input(?:\s+|=)"
    r"(?:(?P<stdin>-)(?=\s|$)|(?P<proc><)(?=\())"
)
_HERE_INPUT_RE = re.compile(
    r"\A[^\n;&|]*?(?:<<<\s*(?P<word>'[^']*'|\"(?:[^\"\\]|\\.)*\"|\S+)"
    r"|<<-?\s*(?P<q>['\"]?)(?P<tag>\w+)(?P=q)[^\n]*\n(?P<body>.*?)\n(?P=tag)(?=\s|$))",
    re.DOTALL,
)


def _graphql_input_query(command: str) -> str | None:
    """The GraphQL query a `gh api graphql --input` call reads from text the guard can see,
    or None: a heredoc or here-string on its own command, an echo or printf piped into it,
    or `--input <(echo|printf …)`. A JSON body gives its `query` field. A body in a file
    the guard cannot read is not judged."""
    if "graphql" not in command or "--input" not in command:
        return None
    m = _GH_API_GRAPHQL_INPUT_RE.search(command)
    if m is None or _executed_surface_view(command)[m.start()] == " ":
        return None
    text = None
    if m.group("proc") is not None:
        span, _ = _extract_dollar_paren(command, m.start("proc"))
        words = (_shell_tokenize(span[2:-1]) or []) if span else []
        if words and words[0] in ("echo", "printf"):
            text = _printed_text(words)
    else:
        here = _HERE_INPUT_RE.match(command[m.end():])
        if here and here.group("body") is not None:
            text = here.group("body")
        elif here:
            word = _shell_tokenize(here.group("word")) or []
            text = word[0] if word else None
        else:
            before = command[:m.start()].rstrip()
            if before.endswith("|") and not before.endswith("||"):
                leg = re.split(r"[;&\n]|\|\|", before[:-1])[-1]
                words = _shell_tokenize(leg) or []
                if words and words[0] in ("echo", "printf"):
                    text = _printed_text(words)
    if not text:
        return None
    try:
        body = json.loads(text)
    except ValueError:
        return text
    return body.get("query") if isinstance(body, dict) and isinstance(body.get("query"), str) else None


def _graphql_input_mutation(command: str) -> str | None:
    """The merge mutation a readable `gh api graphql --input` body calls, or None."""
    query = _graphql_input_query(command)
    if query is None:
        return None
    m = _GRAPHQL_MERGE_MUTATION_RE.search(_GRAPHQL_STRING_RE.sub('""', query))
    return m.group(1) if m else None


def _graphql_input_value(command: str, key: str) -> str | None:
    """The string value of `key` in a GraphQL merge mutation's input, or None."""
    query = _graphql_input_query(command)
    for tok in ([query] if query else []) + (_shell_tokenize(command) or [command]):
        if _GRAPHQL_MERGE_MUTATION_RE.search(tok):
            m = re.search(r"\b" + key + r'\s*:\s*"([^"\\]+)"', tok)
            if m:
                return m.group(1)
    return None


# Updating a pull request's branch merges its base into it: `gh pr update-branch [N]`, or
# `PUT …/pulls/N/update-branch` through gh api.
_GH_PR_UPDATE_BRANCH_RE = re.compile(_GH_PREFIX + r"pr\s+update-branch(?![\w-])")
_GH_API_UPDATE_BRANCH_RE = re.compile(
    _GH_API_PREFIX + r"(?=.*(?:-X|--method)\s+PUT\b).*/pulls/(\d+)/update-branch(?![\w-])",
    re.IGNORECASE,
)
_BRANCH_MERGE_MUTATIONS = frozenset({"mergeBranch", "updatePullRequestBranch"})


def _update_branch_target(leg: str) -> str | None:
    """`""` or the pull request number when a leg updates a pull request's branch (merges
    its base into it), else None."""
    api = _GH_API_UPDATE_BRANCH_RE.search(leg)
    if api:
        return api.group(1)
    if not _GH_PR_UPDATE_BRANCH_RE.search(leg):
        return None
    tokens = _shell_tokenize(leg) or []
    if "update-branch" in tokens:
        rest = tokens[tokens.index("update-branch") + 1:]
        return next((tok for tok in rest if tok.isdigit()), "")
    return ""


def _extract_branch_merge_base(command: str) -> str | None:
    """The branch a branch merge lands on: the REST `base=` (merges) or `branch=`
    (merge-upstream) field, or the GraphQL `base:`."""
    tokens = _shell_tokenize(command) or []
    for i, tok in enumerate(tokens[:-1]):
        if tok in ("-f", "-F", "--field", "--raw-field"):
            key, _, value = tokens[i + 1].partition("=")
            if key in ("base", "branch") and value:
                return value
    return _graphql_input_value(command, "base")


def _is_graphql_merge_query(match: re.Match) -> bool:
    """Whether a `key=value` match is a gh api graphql field value whose query calls a
    merge mutation (judged on the text from the start of its command to the value)."""
    before = match.string[: match.start()]
    if not re.search(r"(?:^|\s)(?:-f|-F|--field|--raw-field)\s+$", before):
        return False
    start = max(before.rfind(sep) for sep in (";", "&", "|", "\n")) + 1
    return _graphql_merge_mutation(before[start:] + match.group(0)) is not None


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

    # CLI push: isolate the token sequence after `push` and require EXACTLY
    # remote + refspec (2 positionals). 0 = implicit push; 1 = remote-only
    # (implicit branch); >2 = multi-ref/chained -> all ambiguous, REFUSE.
    # Positionals are counted via the SHARED _push_positionals helper (#1195
    # OBS-C), which SKIPS the value token of a git-push value-taking option flag
    # (`-o ci.skip`, `--push-option x`, `--repo url`) — the same helper the
    # remote-ref-delete/mass-delete builders use. The prior naive
    # `.split()`-drop-dash-flags miscounted `-o`'s NON-dash value (`ci.skip`) as
    # a 3rd positional -> None -> a faithful force-push carrying a push-option
    # was gated-but-unmintable (an over-block). Skipping the value token
    # recovers the real refspec positional. Only MINT-ENABLING: is_dangerous and
    # the op-class are unchanged; the target is the real refspec, never the -o
    # value (mint==read, both via extract_command_context).
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
    after_push = _shell_tokenize(push_match.group(1))
    if after_push is None:                       # unbalanced quote -> abstain (safe over-block)
        return None
    positionals = _push_positionals(after_push)
    if len(positionals) != 2:
        return None
    refspec = _strip_surrounding_quotes(positionals[1])
    if ":" in refspec:
        return refspec.rsplit(":", 1)[1] or None
    return refspec or None


def _extract_push_to_main_set(command: str) -> str | None:
    """MULTI-ref sibling of the SCALAR push-to-main target (_extract_force_push_target_ref).

    Returns the canonical injective identity of the pushed REFSPECS (>=2), or None
    when fewer than two refspecs are extractable (<2 -> the scalar path owns the
    single-ref form: the BOUNDARY discriminator, so a push-to-main command
    populates EXACTLY ONE of `target_ref` / `push_set` — #1195 OBS-G, the
    branch/branch_set (#1129) precedent transferred). Reuses the netstring
    `_canonical_join` SSOT (#1136): injective by construction and CONTENT-AGNOSTIC
    (colon / `+` / comma / `refs/heads/` content never collides), JSON/str()-safe,
    so mint==read symmetry and the token round-trip both hold. The identity is
    over the FULL refspec tokens (`feature:main` kept, never its dst alone) — the
    tighter binding: src:dst pairs bind exactly, and a `+refspec` element stays
    distinct from its plain spelling. The refspecs are `_push_positionals(...)[1:]`
    — the remote positional is SKIPPED (a remote-AGNOSTIC identity, mirroring the
    scalar target_ref, which binds the ref only; security-ratified). Tokenization
    mirrors the push-to-main DETECTION arm in `_flag_condition_danger_op`
    (`.split()` + the K flag bound + `push\\s` well-formedness), so the refspecs
    bound here are the SAME tokens the detection predicate walked. Fail-safe:
    unparseable / procsub (`_executable_prefix` None) or a flag-flood (>K) -> None
    -> the command stays gated-but-unmintable (a residual over-block, never an
    over-broad token).
    """
    prefix = _executable_prefix(command)
    if prefix is None:
        return None                     # fail-safe (unparseable / procsub)
    _pm = re.search(_GIT_PREFIX + r"push\s(.*)$", prefix)
    if _pm is None:
        return None                     # well-formed push only (excludes `push--force` glue)
    _tail = _pm.group(1).split()
    if sum(1 for t in _tail if t.startswith("-")) > _MAX_GLOBAL_FLAG_TOKENS:
        return None                     # perf bound (mirrors the OBS-D/E flag walk)
    refspecs = [_strip_surrounding_quotes(r) for r in _push_positionals(_tail)[1:]]
    if len(refspecs) < 2:
        return None                     # <2 -> the scalar target_ref path owns it (boundary)
    return _canonical_join(sorted(set(refspecs)))   # sort+dedup+canonical (branch_set mirror)


# #1195 OBS-I — the REF-SCOPE-NEUTRAL flag allowlist for force_push_set (the
# ALLOWLIST-INVERSION guard; a SECURITY-OWNED membership surface like
# PRIVILEGED_FLAGS — any future addition is a reviewed, deliberate act; the cert
# pins the exact membership). force_push_set is populated ONLY when EVERY flag
# token resolves into this set; ANYTHING else — the delete/mass op-triggers
# (--delete/-d/--mirror/--prune), the scope expanders (--tags/--follow-tags/
# --all/--branches), --recurse-submodules, -n/--dry-run, --force-with-lease on a
# force-classified command, --no-force, the exotic --repo/--receive-pack/--exec,
# the `--` end-of-options marker, and every unknown/FUTURE flag — fails SAFE to
# None = gated-but-unmintable. Rationale: `_push_positionals` skips flags BY
# DESIGN, so a positional set-identity is structurally BLIND to flag-borne
# ref-scope semantics (`--force --delete origin main feature` classifies
# force-push — force outranks delete — and would collide byte-identically with
# the pure force set); such semantics can only be GUARDED (None), never encoded.
# Allowlist incompleteness costs an exotic residual over-block, never a hole.
_FORCE_PUSH_NEUTRAL_LONG = frozenset({
    "--force",                     # the op itself + the forced-form trigger
    "--no-verify",                 # bound via force-push PRIVILEGED_FLAGS (a2 discriminates)
    "--verbose", "--quiet", "--progress", "--no-progress", "--porcelain",  # output-only
    "--set-upstream",              # local tracking config
    "--atomic",                    # transaction semantics, same ref set
    "--ipv4", "--ipv6", "--thin", "--no-thin",                             # transport
    "--signed", "--no-signed",     # transport signing
    "--push-option",               # server-side message (value inert to ref scope)
})
_FORCE_PUSH_NEUTRAL_SHORT = frozenset("fvqu46")   # -f -v -q -u -4 -6 (cluster letters)


def _resolve_neutral_force_tail(_tail: list) -> "tuple[list, bool] | None":
    """ONE raw-token walk over the post-`push` tail (#1195 OBS-I): resolves git's
    getopt-style value-taking `-o` in ANY cluster position, runs the
    ref-scope-neutrality census, and detects the class-wide force flag —
    returning (resolved_tail, force) or None when ANY flag token is non-neutral,
    unknown, or malformed (fail-safe: gated-but-unmintable, never an over-broad
    token).

    WHY RAW TOKENS (load-bearing): `_normalized_flags` is CONDITION-SCOPED — it
    reports only the flags the danger conditions test and is BLIND to
    --tags/--all/-v/... — so a census built on it would silently pass a
    scope-expansion smuggle. WHY the value resolution lives HERE: `-o` takes a
    VALUE; getopt semantics make everything after `o` in a short cluster (or the
    next token when `o` is last) that value, NOT flags — `-fvo msg` = -f -v
    -o=msg; `-fov` = -f -o=v. The resolved tail (value tokens dropped, the
    `o`+value consumed from clusters) is what the SHARED `_push_positionals`
    then gathers, so the census and the positional gather agree on value tokens
    BY CONSTRUCTION — while `_push_positionals` itself stays byte-untouched (its
    other consumers keep today's behavior). The value-short set is
    BOUNDED-COMPLETE = {-o}: every other git-push short is boolean.

    O-FIRST GLUED-VALUE AMBIGUITY (lead+security ruling): a glued rest-of-token
    value MINTS only when `f` is unambiguously present BEFORE the `o` in the
    SAME cluster (`-fov`, `-fvoMSG` — force established either way you read the
    tail). An o-first/no-f-before-o glued form (`-ofv`, `-vox`) is the shape
    where git's parse (o consumes the rest as a VALUE — no force) diverges from
    the pre-existing cluster-blind detection (which sees the letters as flags):
    the OPERATION itself is parse-dependent, so minting ANY identity would be
    wrong under one reading — fail SAFE to None (an exotic, low-good-faith
    residual over-block; the detection mis-parse itself is out of scope here)."""
    resolved: list = []
    force = False
    i, n = 0, len(_tail)
    while i < n:
        tok = _tail[i]
        if not tok.startswith("-") or tok == "-":
            resolved.append(tok)             # positional (or the bare '-' token)
            i += 1
            continue
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name not in _FORCE_PUSH_NEUTRAL_LONG:
                return None                  # incl. bare `--` + unknown/future flags
            if name == "--force":
                force = True
            if name == "--push-option" and "=" not in tok:
                if i + 1 >= n:
                    return None              # separate-value form missing its value
                i += 2                       # skip the value token
                continue
            i += 1
            continue
        # Short cluster: boolean letters until a value-`o`; at `o` the rest of the
        # token (or the next token when `o` is last) is its VALUE — never flags.
        consumed_next = False
        cluster_bad = False
        cluster_force = False
        for j in range(1, len(tok)):
            ch = tok[j]
            if ch == "o":
                if j == len(tok) - 1:        # `-...o` last: next token is the value
                    if i + 1 >= n:
                        cluster_bad = True   # missing value (malformed) — fail-safe
                    else:
                        consumed_next = True
                elif not cluster_force:
                    # o-first / no-f-before-o GLUED value (`-ofv`, `-vox`): git's
                    # parse and the cluster-blind detection diverge on WHAT THE
                    # OPERATION IS — ambiguous, fail SAFE to None (lead ruling).
                    cluster_bad = True
                break                        # rest-of-token (if any) is the value
            if ch not in _FORCE_PUSH_NEUTRAL_SHORT:
                cluster_bad = True
                break
            if ch == "f":
                force = True
                cluster_force = True
        if cluster_bad:
            return None
        i += 2 if consumed_next else 1
    return resolved, force


def _extract_force_push_set(command: str) -> str | None:
    """MULTI-ref sibling of the SCALAR force-push target — the force-push analog
    of _extract_push_to_main_set (#1195 OBS-I, reversing the OBS-G scope boundary
    under its own ratified proof). Same skeleton (fail-safe prefix, single
    guarded `push\\s` search, K flag bound, remote-agnostic positionals[1:],
    `<2 -> None` scalar boundary, injective netstring `_canonical_join` identity)
    PLUS the three OBS-I-specific layers:
      (a) the ALLOWLIST-INVERSION census + the value-`-o` resolution + the force
          trigger — ONE raw-token walk (_resolve_neutral_force_tail);
      (b) FORCED-FORM NORMALIZATION: class-wide --force/-f upgrades every element
          to its `+` form so the identity tracks WHICH REFS GET FORCED — the
          mixed-set plain->forced collision cure (`+feature main` stays DISTINCT
          from `--force +feature main`) while `--force feature main` ==
          `+feature +main` (ratified: the same operation, one identity);
      (c) the `:`-element DELETE GUARD (defense-in-depth for the refspec-syntax
          channel when force wins classification): None on any ':'-leading
          element after '+'-strip — a delete can never smuggle into a mintable
          force set.
    """
    prefix = _executable_prefix(command)
    if prefix is None:
        return None                     # fail-safe (unparseable / procsub)
    _pm = re.search(_GIT_PREFIX + r"push\s(.*)$", prefix)
    if _pm is None:
        return None                     # well-formed push only (excludes glue)
    _tail = _pm.group(1).split()
    if sum(1 for t in _tail if t.startswith("-")) > _MAX_GLOBAL_FLAG_TOKENS:
        return None                     # perf bound (mirrors the OBS-D/E flag walk)
    _resolved = _resolve_neutral_force_tail(_tail)
    if _resolved is None:
        return None                     # non-neutral / unknown / malformed flag
    _clean_tail, _force = _resolved
    refspecs = [_strip_surrounding_quotes(r) for r in _push_positionals(_clean_tail)[1:]]
    if len(refspecs) < 2:
        return None                     # <2 -> the scalar target_ref path owns it
    if _force:
        refspecs = [r if r.startswith("+") else "+" + r for r in refspecs]
    if any(r.lstrip("+").startswith(":") for r in refspecs):
        return None                     # delete-refspec guard (fail-safe)
    return _canonical_join(sorted(set(refspecs)))


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


# #1203 — target-blind sentinel identities for the IMPLICIT destructive forms whose
# real target is RUNTIME state absent from the command string (a bare force-push
# rewrites f(push.default, @{upstream}); a bare `gh pr merge` merges the current-branch
# PR). NUL-framed so the value is REF-ILLEGAL — no git ref/branch or gh PR identity can
# contain a NUL, so a sentinel can NEVER collide with a real target_ref/pr_number
# (defense-in-depth on top of the distinct typed key). Each is its OWN context key
# (KD-2), never a value in target_ref/pr_number, so op-type-first + distinct-key keeps
# an implicit-form token from cross-authorizing an explicit `--force origin main` /
# `gh pr merge 42` and vice-versa. The KD-4 mutual-exclusivity holds by construction:
# a sentinel populates ONLY when the explicit target key for that op is absent.
_FORCE_PUSH_IMPLICIT_SENTINEL = "\x00implicit-force-push"
_MERGE_IMPLICIT_SENTINEL = "\x00implicit-merge"


def _implicit_force_push_identity(command: str) -> str | None:
    """The `force_push_implicit` mint identity for a CLEAN implicit-current-branch
    force-push (SET A), or None (multi-ref / unparseable). #1203 cycle-3 REMOTE-QUALIFIED
    the identity (so `--force origin` ⊥ `--force upstream`); cycle-4 reframes the
    separator to the NETSTRING SSOT so a URL-remote (which contains `:`) ALSO mints —
    closing the pre-existing URL-remote over-block — while KEEPING injectivity and
    WITHOUT any un-gate:

      - 0 positionals (bare `git push --force`/`-f`) → the PLAIN
        `_FORCE_PUSH_IMPLICIT_SENTINEL`: the remote is ALSO runtime-unknowable
        (push.default / @{upstream}), so the identity stays fully target-blind.
      - exactly 1 positional — ANY <repository>: a named remote (`origin`), a URL
        (`git@github.com:o/r.git` / `ssh://…` / `https://…`), or even a refspec-SHAPED
        token → `_FORCE_PUSH_IMPLICIT_SENTINEL + _canonical_join([<remote>])`:
        REMOTE-qualified, BRANCH-still-blind (KD-3 residual). git parses a LONE push
        positional as the <repository> regardless of shape, so ALL 1-positionals are
        remotes; a refspec-shaped one is a NON-RUNNABLE repository spelling that binds
        HARMLESSLY (self-authorizing-only — its token round-trips ONLY to itself).

    The NETSTRING (`_canonical_join`, the branch_set/mass_target SSOT — `len:content`
    framing) is content-agnostic + INJECTIVE by construction for ANY remote string incl.
    `:`/`@`/NUL, which is why cycle-4 can DROP cycle-3's `:`/`refs/`/`HEAD`/`+`
    single-positional refusal: injectivity no longer needs the remote to be `:`-free.
    Distinct remotes → distinct frames; a qualified value (…`<len>:<remote>`) can never
    set-equal the bare `_FORCE_PUSH_IMPLICIT_SENTINEL`.

    Consulted by extract_command_context's force-push branch ONLY after
    _extract_force_push_target_ref (scalar) AND _extract_force_push_set (multi-ref) both
    returned None (KD-4 mutual-exclusivity), so an explicit-ref/multi-ref command never
    reaches here — a non-None identity mints the sentinel for a form that would otherwise
    be gated-but-unmintable (the cardinal over-block). is_dangerous is computed SEPARATELY
    (unchanged) — this is a MINT-only change, never an un-gate.

    None (fail-safe, gated-but-unmintable) for: MULTI-positional (>=2 — the explicit-ref
    path owns target_ref, UNCHANGED); a degenerate empty/whitespace-only positional; and
    any unparseable / unbalanced-quote / procsub form. Positionals are counted via the
    SHARED _push_positionals (value-flag skip), so a `-o ci.skip` push-option never
    inflates the count NOR leaks as the remote.

    mint==read BY CONSTRUCTION: both hook arms derive via extract_command_context and
    consume `force_push_implicit` generically (read `_both_present_equal`, mint
    `_target_value`); the value is a pure function of the command string (no runtime
    resolution), so the netstring value needs NO read/mint edit."""
    after = _tokens_after_push(command)
    if after is None:
        return None                               # no push token / unbalanced → abstain
    positionals = _push_positionals(after)
    if len(positionals) == 0:
        return _FORCE_PUSH_IMPLICIT_SENTINEL      # bare — remote ALSO runtime-unknowable
    if len(positionals) == 1:
        remote = _strip_surrounding_quotes(positionals[0])
        if not remote.strip():
            return None                           # degenerate empty/whitespace → abstain
        # ANY 1-positional is the <repository> by git grammar — a named remote, a URL
        # (`git@github.com:o/r.git` / `ssh://…` / `https://…`), OR a non-runnable
        # refspec-SHAPED token; the NETSTRING _canonical_join binds it INJECTIVELY (incl.
        # a `:`-bearing URL), so cycle-4 DROPS cycle-3's `:`/`refs/`/`HEAD`/`+` refusal —
        # injectivity no longer needs the remote to be `:`-free, and a refspec-shaped
        # bind is self-authorizing-only (non-runnable; its token round-trips ONLY to
        # itself). This is the URL-remote over-block close (#1203 cycle-4).
        return _FORCE_PUSH_IMPLICIT_SENTINEL + _canonical_join([remote])
    return None                                   # >=2 positional → explicit-ref path owns it


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
# would double-bind and needlessly over-block.
#
# THE EXCLUSION IS PER-CLASS, NOT GLOBAL — it is the flag's ROLE ON THAT CLASS
# that decides, and the same flag can have different roles on different classes.
# The rule: EXCLUDE A TRIGGER FROM THE CLASS IT TRIGGERS; BIND IT WHERE IT IS A
# MODIFIER. Worked instances, all three of which look like exceptions until the
# rule is stated this way:
#   - --delete-branch/-d TRIGGERS `close`, but on gh pr MERGE it is a post-merge
#     SIDE-EFFECT (deletes the source branch), so it IS bound on `merge`. (And -d
#     on merge is a DIFFERENT op from -D branch force-delete; op-class scoping
#     keeps them from being conflated.) It is ALSO bound on `close` itself, for
#     the narrower reason spelled out at that entry: op_type folds bare-close and
#     close --delete-branch into ONE op, so there the trigger does not
#     discriminate and the flag binding is what separates them.
#   - --mirror/--prune TRIGGER `remote-mass-delete`, but on `remote-ref-delete`
#     they are scope-widening MODIFIERS, so they ARE bound there — that binding
#     is what stops a single-branch-delete approval from authorizing a wholesale
#     remote wipe.
# So "is this flag an op-trigger?" is the WRONG question at this table. The right
# one is "is it a trigger OF THIS CLASS?"
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
        # The op-TRIGGER here is --delete/-d/empty-source colon, already bound via
        # op_type. These three are MODIFIERS on top of that trigger, so binding them
        # adds a dimension the identity did not previously capture.
        #
        # --mirror/--prune are LOAD-BEARING on this class. A single-ref delete's
        # identity is otherwise the bare ref (`main`), so an approval for
        # `git push origin :main` (delete ONE branch) set-equaled an execution of
        # `git push origin --mirror :main` — which deletes EVERY remote ref with no
        # local counterpart. Single-branch approval, wholesale remote wipe. The
        # single-ref extractor wins the boundary discriminator, so the scope-changing
        # flag rode an identity that did not capture it. Binding closes it.
        "--mirror":    (("--mirror",), False),
        "--prune":     (("--prune",), False),
        # bypasses the pre-push hook — a privilege, not a target change
        "--no-verify": (("--no-verify",), False),
    },
    "remote-mass-delete": {
        # DELIBERATELY NARROWER THAN ITS SIBLING, and the asymmetry is not cosmetic:
        # exclude a trigger from the class it triggers; bind it where it is a modifier.
        # --mirror/--prune are the op-TRIGGER for this class, and _extract_mass_delete_target
        # EMBEDS them in the identity netstring (`git push --mirror origin` binds
        # '8:--mirror6:origin', `--prune` binds '7:--prune6:origin'). So a --mirror/--prune
        # escalation on this class ALREADY refuses at the identity layer, and binding them
        # here changes no outcome TODAY — a cert row for it would be vacuous, and on a
        # control whose dominant failure mode is checks that prove nothing, an entry that
        # reads as protection while proving nothing is a real cost.
        #
        # VACUOUS TODAY IS NOT THE SAME CLAIM AS INERT, and the difference decides the
        # design. Binding them here WOULD matter if the identity ever coarsened (see the
        # COUPLING note below) — so the choice is not "redundant vs useful", it is
        # "auditable now, with the coupling pinned" vs "silently defended, unverifiably".
        # We take the first: narrow entry + a load-bearing pin on what it depends on.
        #
        # COUPLING — LOAD-BEARING, pinned by test, not left implicit. This narrowness
        # DEPENDS on mass_target continuing to EMBED the flag. Measured under a coarsened
        # mass_target (remote only): the split binding lets the --mirror/--prune escalation
        # OPEN, while a symmetric binding would keep it closed. So the redundancy is NOT
        # inert — split is chosen for auditability and is safety-equivalent to symmetric
        # ONLY while that embedding holds.
        # REMEDY: if you coarsen mass_target, you MUST bind --mirror and --prune HERE in
        # the same commit. See test_mass_target_embeds_the_scope_flag_in_its_identity.
        "--no-verify": (("--no-verify",), False),
    },
    "branch-merge": {
        # No bound flags: merging a branch through the API has no privileged modifier.
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


def _guarded_substitution_body(command: str) -> str | None:
    """The body of the one substitution (`$(…)`, `<(…)`, `>(…)`, backticks) that holds a
    guarded operation, when that body is exactly one guarded command and nothing outside
    the substitutions is guarded; else None."""
    if "(" not in command and "`" not in command:
        return None
    view = _mask_quotes_keeping_substitutions(command)
    outside, bodies, i, n = list(command), [], 0, len(command)
    while i < n:
        if view[i] == "\\":
            i += 2
            continue
        if view.startswith(("$(", "<(", ">("), i):
            span, end = _extract_dollar_paren(command, i)
            body = None if span is None else span[2:-1]
        elif view[i] == "`":
            span, end = _extract_backtick(command, i)
            body = None if span is None else span[1:-1]
        else:
            i += 1
            continue
        if body is None:
            return None
        bodies.append(body)
        outside[i:end] = " " * (end - i)
        i = end
    guarded = [b for b in bodies if detect_command_operation_type(b) is not None]
    if len(guarded) != 1 or detect_command_operation_type("".join(outside)) is not None:
        return None
    if _single_destructive_leg(guarded[0]) != guarded[0].strip():
        return None
    return guarded[0]


def extract_command_context(command: str, flag_scan_text: str | None = None) -> dict:
    """Extract operation context FROM A COMMAND STRING (never prose).

    The shared SSOT both merge-guard hooks call. A key is PRESENT only when
    positively extracted; ABSENT otherwise (absence — NOT a None value — is the
    fail-closed signal). Possible keys:
        operation_type: "merge" | "close" | "force-push" | "branch-delete"
                        | "push-to-main" | "remote-ref-delete" | "remote-mass-delete"
                        | "branch-protection"
        pr_number:  str  (merge / close) — the EXPLICIT PR/target identity: a bare
                     number, OR (close + merge #1203 M1) a `url:<host>/<owner>/<repo>#N`
                     / `branch:<name>` namespaced identity via the shared
                     _classify_pr_target; op-type-first keeps merge⊥close
        merge_implicit: str (merge IMPLICIT truly-bare `gh pr merge` #1203) — a
                     target-blind NUL-framed sentinel (_MERGE_IMPLICIT_SENTINEL),
                     populated ONLY on op_type=='merge' when pr_number is absent AND the
                     command is truly-bare (_is_bare_cli_merge: CLI shape + ZERO real
                     positional target; an explicit branch/url binds pr_number above, a
                     >=2-positional form abstains; never close); distinct key so it never
                     cross-authorizes an explicit pr_number (number/branch/url)
        branch:     str  (branch-delete — SINGLE target, exactly 1 positional)
        branch_set: str  (branch-delete — MULTI target #1129, >=2 positionals) —
                     canonical sort+dedup+quote-strip names via the shared netstring _canonical_join (`len:name` framing,
                     injective by construction, content-agnostic — no delimiter collision)
        target_ref: str  (force-push / push-to-main, KD-6; remote-ref-delete #1062a)
        push_set:   str  (push-to-main MULTI-ref #1195 OBS-G, >=2 refspecs) —
                     canonical sort+dedup FULL-refspec identity via the shared
                     netstring _canonical_join (remote-agnostic; the scalar
                     target_ref owns the single-ref form — exactly one populated)
        force_push_set: str (force-push MULTI-ref #1195 OBS-I, >=2 refspecs) —
                     canonical FORCED-NORMALIZED identity (class-wide --force
                     upgrades elements to '+'-form) gated by the ref-scope-neutral
                     flag allowlist; exactly one of target_ref / push_set /
                     force_push_set populated per command
        force_push_implicit: str (force-push IMPLICIT current-branch #1203) — a
                     branch-blind NUL-framed sentinel, REMOTE-QUALIFIED (cycle-3 #1+#2):
                     bare form = `_FORCE_PUSH_IMPLICIT_SENTINEL` (remote also runtime-
                     unknowable); 1-plain-remote form = `<SENTINEL>:<remote>` so
                     `--force origin` ⊥ `--force upstream`. Populated ONLY on
                     op_type=='force-push' when target_ref AND force_push_set are both
                     absent (the clean 0/1-plain-remote forms; SET B → None → absent);
                     distinct key so it never cross-authorizes an explicit
                     target_ref/force_push_set (via _implicit_force_push_identity)
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
    # A script given a whole guarded command, literally or through a literal variable, is
    # read as that command, so its target binds; one given several binds none of them.
    script_arg = (_script_argv_guarded_command(command)
                  or _script_argv_guarded_command(_expand_literal_variables(command)))
    if script_arg is not None:
        if is_compound_destructive_command(script_arg):
            return context
        return extract_command_context(script_arg, flag_scan_text)
    # A guarded command run by a substitution is read as that command, so its target binds.
    body = _guarded_substitution_body(command)
    if body is not None:
        return extract_command_context(body, flag_scan_text)
    op_type = detect_command_operation_type(command)
    if op_type is None:
        # A guarded command written through a variable (`CMD=gh; $CMD pr merge 5`) is read
        # with its variables expanded, as the refusal reads it.
        expanded = _expand_literal_variables(command)
        if expanded != command:
            return extract_command_context(expanded, flag_scan_text)
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
        # close accepts {<number>|<url>|<branch>} — bind whichever the command
        # carries so EVERY faithful close form MINTS (not just the number form).
        # merge keeps the number-only extractor (+ the #1096 API pulls/<N> path).
        if op_type == "close":
            pr_number = _extract_close_target(command)
        else:
            pr_number = _extract_pr_number(command)
            if pr_number is None:
                pr_number = _extract_api_merge_pr(command)   # #1096 API pulls/<N>/merge
            if pr_number is None:
                # #1203 M1 — the EXPLICIT `gh pr merge <branch>`/`<url>` target. Mirrors
                # close's {number|url|branch} bind via the SHARED _classify_pr_target, so
                # a branch/url merge binds a DISTINCT explicit identity (branch:<name> /
                # url:<host>/<owner>/<repo>#N) instead of the target-blind sentinel —
                # closing the M1 cross-auth (every non-numeric merge previously collapsed
                # to one identity). The positional is isolated by the MERGE-value-flag
                # walk (_extract_merge_target), NOT close's, so a value like
                # `--subject foo` is not mis-read as the branch. Reuses close's hardened
                # host-qualified/injective identity logic; bound to pr_number (already a
                # four-site mint key) so op-type-first keeps merge⊥close.
                pr_number = _extract_merge_target(command)
        if pr_number is not None:
            context["pr_number"] = pr_number
        elif op_type == "merge" and _is_bare_cli_merge(command):
            # #1203 KD-8 + M1 — TARGET-BLIND sentinel for ONLY the TRULY-BARE CLI
            # `gh pr merge` (and `--admin`/`--squash` — flags-only, ZERO positional
            # target), whose target is the current-branch PR = runtime state resolvable
            # only by a network gh-API call at mint time (infeasible in-hook). Guarded:
            #   (1) MERGE-ONLY (op_type=="merge"): #1134 dissolved bare `gh pr close`, so
            #       a close must NEVER mint this — the populate-site can't carry the key.
            #   (2) TRULY-BARE ONLY (_is_bare_cli_merge — CLI shape AND 0 real positionals
            #       via the merge value-flag walk): an EXPLICIT branch/url target already
            #       bound pr_number above (the M1 cross-auth fix), and a >=2 ambiguous
            #       count abstains (gated-but-unmintable, cross-auth-safe) — never the
            #       sentinel. The CLI-shape check also excludes an API merge (which always
            #       carries an explicit endpoint PR).
            # DISTINCT typed key (not pr_number) so an implicit-merge token cannot cross-
            # authorize an EXPLICIT merge — now TRUE for number AND branch AND url (every
            # explicit form binds pr_number; op-type-first keeps merge⊥close). is_dangerous
            # is unchanged (still True): target-PRECISION mint, never un-gating.
            context["merge_implicit"] = _MERGE_IMPLICIT_SENTINEL
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
        elif op_type == "push-to-main":
            # DISTINCT-set boundary (#1195 OBS-G): the MULTI-ref push-to-main
            # identity, populated ONLY when the scalar refused (multi-ref ->
            # target_ref None). Exactly ONE of `target_ref` / `push_set` /
            # `force_push_set` is populated per command — the scalar's `!=2 ->
            # None`, the sets' `<2 -> None`, and this op split are mutually
            # exclusive by construction — so no scalar/set or cross-op
            # cross-authorization window exists.
            push_set = _extract_push_to_main_set(command)
            if push_set is not None:
                context["push_set"] = push_set
        else:
            # force-push (#1195 OBS-I, reversing the OBS-G scope boundary under
            # its own ratified proof): the MULTI-ref FORCED-NORMALIZED set
            # identity, gated by the ref-scope-neutral flag ALLOWLIST (see
            # _extract_force_push_set) — a delete/mass/scope-expander/unknown
            # flag on the execution or the approval derives force_push_set=None
            # and fails SAFE to gated-but-unmintable / read-refused. The scalar
            # _extract_force_push_target_ref above remains byte-untouched;
            # multi-ref DELETE spellings still never mint (delete never gains a
            # set — they classify remote-ref-delete/mass, not force-push, and a
            # force-masked delete flag fails the allowlist).
            force_push_set = _extract_force_push_set(command)
            if force_push_set is not None:
                context["force_push_set"] = force_push_set
            else:
                # #1203 KD-2/KD-4 + cycle-3 — REMOTE-QUALIFIED target-blind sentinel for
                # the implicit current-branch force-push (`git push --force`, `-f`,
                # `--force origin`, …), whose BRANCH target is runtime state absent from
                # the string. Reached ONLY when target_ref (scalar) AND force_push_set
                # (multi-ref) are BOTH absent, on op_type=='force-push' (never the
                # push-to-main path above), so an explicit-ref/multi-ref command never
                # carries this sentinel — the mutual-exclusivity is structural. Distinct
                # typed key (not a target_ref value) so op-type-first + distinct-key keeps
                # this token from cross-authorizing an explicit `--force origin main`.
                # cycle-3 (#1+#2): the identity now binds the EXPLICIT remote (`…:origin`)
                # for a 1-positional form so `--force origin` ⊥ `--force upstream`; the
                # bare form stays fully blind (remote runtime-unknowable). SET B →
                # identity None → key absent → gated-but-unmintable (UNCHANGED). The
                # scalar/set extractors above stay BYTE-UNTOUCHED. is_dangerous is
                # unchanged (still True): target-PRECISION (mint the over-blocked form),
                # never un-gating.
                fpi = _implicit_force_push_identity(command)
                if fpi is not None:
                    context["force_push_implicit"] = fpi
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
    elif op_type == "branch-merge":
        base = _extract_branch_merge_base(command)
        if base is not None:
            context["target_ref"] = base
        else:
            pr = next((t for leg in _split_into_legs(command)
                       if (t := _update_branch_target(leg))), None)
            if pr:
                context["pr_number"] = pr
    if op_type in ("merge", "branch-merge") and not (
            {"pr_number", "target_ref"} & context.keys()):
        # A GraphQL merge names its pull request by node id, a key no numeric merge
        # token carries.
        node_id = _graphql_input_value(command, "pullRequestId")
        if node_id is not None:
            context["pr_node_id"] = node_id
    if op_type in ("merge", "branch-merge"):
        # A merge sent as a GraphQL mutation names that mutation, so the approval side can
        # bind it by its text even when its input carries no id.
        mutation = _graphql_input_mutation(command) or next(
            (name for leg in _split_into_legs(command) if (name := _graphql_merge_mutation(leg))), None)
        if mutation is not None:
            context["graphql_mutation"] = mutation
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
# Direct push to a default branch (bypasses PR merge) — the former HEAD:main / HEAD:master
# and bounded-flag-walk main / master literals were REMOVED (#1195 OBS-E): the read floor
# recognizes push-to-main via `_flag_condition_danger_op` (its sole consumer here,
# _stripped_surface_danger, calls it), using the UNIFIED _PUSH_MAIN_DST_RE refspec-DST
# predicate — the SAME arm detect uses, so mint==read holds by construction with no second
# copy to drift. The unified predicate fixes both the prefix over-block (main-release) and
# the <src>:dst / full-ref under-block (feature:main / refs/heads/main) the literals had.
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


# An interpreter reads its PROGRAM from stdin when it gets no code argument: no -c/-e/-r/-m
# and no script path, only flags or a lone `-`. Its stdin feed (a pipe into it, a here-string,
# a heredoc) is then that interpreter's code, and it can run a shell exactly when it names an
# exec primitive.
# The interpreter heads, as one alternation: the single source for the stdin feed pattern,
# the head finder and _INTERPRETER_HEADS. Same per-FAMILY anchoring as
# _SHELL_STRING_EXECUTORS (python-config is no head); a versioned or suffixed binary
# (php8.2, perl5.36, node18, python3.13t) is the same interpreter.
_INTERPRETER_HEAD_ALTERNATION = (
    r"python[0-9.]*t?|pypy[0-9.]*t?|perl[0-9.]*|ruby[0-9.]*|nodejs[0-9.]*|node[0-9.]*"
    r"|php[0-9.]*(?:-cgi)?|bun|deno|lua[0-9.]*|luajit|R|Rscript|osascript|tclsh[0-9.]*|wish[0-9.]*"
    r"|awk|gawk|mawk|nawk"
)
_STDIN_INTERPRETER = (
    r"(?<![\w./-])(?:[^\s|;&<>()]*/)?"
    r"(?P<head>" + _INTERPRETER_HEAD_ALTERNATION + r")"
    r"(?P<sub>[ \t]+(?:eval|run)(?=[ \t]))?"         # deno's eval / run subcommand
    r"(?P<flags>(?:[ \t]+-[\w-]*)*)"
    r"(?:[ \t]+-(?:[ \t]+[^\s<>|;&()]+)+)?[ \t]*"   # `-` then the program's argv
)
_CODE_ARG_FLAGS = frozenset({"-c", "-e", "-E", "-r", "-m", "-f", "--eval", "--command"})
_FED_HEREDOC_RE = re.compile(
    _STDIN_INTERPRETER + r"<<-?[ \t]*(?P<q>['\"]?)(?P<tag>\w+)(?P=q)[^\n]*\n(?P<body>.*?)\n[ \t]*(?P=tag)[ \t]*(?:\n|$)",
    re.DOTALL,
)
_FED_HERESTRING_RE = re.compile(
    _STDIN_INTERPRETER + r"<<<[ \t]*(?P<body>'[^']*'|\"(?:[^\"\\]|\\.)*\"|\S+)"
)
_PIPE_INTO_INTERPRETER_RE = re.compile(r"\|[ \t]*" + _STDIN_INTERPRETER + r"(?=$|[;&|)\n])")


def _reads_program_from_stdin(m: re.Match) -> bool:
    """No code argument: no -c/-e/… flag and not deno's `eval` subcommand."""
    if (m.group("sub") or "").strip() == "eval":
        return False
    return not any(f in _CODE_ARG_FLAGS for f in m.group("flags").split())


def _stdin_fed_code(command: str) -> list[tuple[str, str]]:
    """(interpreter head, program text) for each program an interpreter reads from stdin
    in this command: each heredoc or here-string body after an interpreter given no code
    argument, and, for a pipe into one, the quoted text upstream of it (what `echo '…'`
    or `printf '…'` emits) plus any heredoc body (`cat <<EOF | python3`)."""
    code = [(m.group("head"), m.group("body")) for m in _FED_HEREDOC_RE.finditer(command)
            if _reads_program_from_stdin(m)]
    code += [(m.group("head"), _unquote_literal(m.group("body")))
             for m in _FED_HERESTRING_RE.finditer(command)
             if _reads_program_from_stdin(m)]
    if not _PIPE_INTO_INTERPRETER_RE.search(_executed_surface_view(command)):
        return code
    # A pipe feeds an interpreter only what its OWN pipeline produces: take the quoted
    # text and heredoc bodies of that pipeline, not of other commands in the line.
    view, heredocs = _pipeline_view(command)
    start = 0
    for sep in [*_PIPELINE_SEPARATOR_RE.finditer(view), None]:
        end = sep.start() if sep else len(command)
        for m in _PIPE_INTO_INTERPRETER_RE.finditer(view[start:end]):
            if not _reads_program_from_stdin(m):
                continue
            head = m.group("head")
            code += [(head, _unquote_literal(q)) for q in _STRING_LITERAL_RE.findall(command[start:end])]
            code += [(head, h.group("body")) for h in heredocs if start <= h.start() < end]
        start = sep.end() if sep else end
    return code


# Separators between pipelines: `;`, `&&`, `||`, a background `&`, a newline. A single
# `|` joins commands INTO a pipeline and is not one.
_PIPELINE_SEPARATOR_RE = re.compile(r"&&|\|\||;|(?<![|>&<])&(?![&>])|\n")


def _pipeline_view(command: str) -> tuple[str, list]:
    """`command` with quoted text and heredoc bodies blanked, same length, so pipeline
    separators are found only in shell structure; plus the heredoc matches."""
    view = list(_leg_slicing_view(command))
    heredocs = list(_ANY_HEREDOC_RE.finditer(command))
    for h in heredocs:
        for k in range(h.start("body"), h.end()):
            view[k] = " "
    return "".join(view), heredocs


# A shell comment: `#` at the start of a word, through the end of the line. Run on a
# view with quoted text and heredoc bodies blanked, so a `#` inside them never counts.
_COMMENT_RE = re.compile(r"(?:^|(?<=[\s;&|(]))#[^\n]*", re.MULTILINE)
_PRINT_ONLY_HEADS = frozenset({"echo", "printf"})


def _code_arg_programs(command: str) -> list[tuple[str, str]]:
    """(interpreter head, program text) for each program given on an interpreter's
    command line (-c / -e / -r / deno eval / an awk program), wherever the interpreter
    sits in the command (after sudo, env, uv run, …). Quoted text, heredoc bodies and
    shell comments are never read as a head, and neither is a word `echo` or `printf`
    only prints."""
    out = []
    view = _COMMENT_RE.sub(lambda c: " " * len(c.group()), _pipeline_view(command)[0])
    for m in _INTERPRETER_WORD_RE.finditer(view):
        leg_start = max((op.end() for op in _COMPOUND_OPS_RE.finditer(view, 0, m.start())), default=0)
        words = view[leg_start:m.start()].split()
        if words and os.path.basename(words[0]) in _PRINT_ONLY_HEADS:
            continue
        end = _COMPOUND_OPS_RE.search(view, m.end())
        tokens = _shell_tokenize(command[m.start():end.start() if end else len(command)])
        if not tokens:
            continue
        head = os.path.basename(tokens[0])
        program = _program_from_tokens(head, tokens)
        if program:
            out.append((head, program))
    return out


def _unquote_literal(text: str) -> str:
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"" else text


_ANY_HEREDOC_RE = re.compile(
    r"<<-?[ \t]*(['\"]?)(?P<tag>\w+)\1[^\n]*\n(?P<body>.*?)\n[ \t]*(?P=tag)[ \t]*(?:\n|$)", re.DOTALL
)
# A call whose name names an exec primitive, then its first argument. Guarded text passed
# anywhere else (a variable, a later argument, a data structure) is data to this rule.
_PRIMITIVE_CALL_RE = re.compile(r"(?<![\w$])((?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*)(?:[ \t]*\([ \t]*|[ \t]+)")
# Tcl's `exec` takes a bare command line, not a literal: `exec sh -c "…"` in statement
# position (line start, after `;`, or inside `[…]`).
_TCL_EXEC_RE = re.compile(r"(?m)(?:^|[;\[{])[ \t]*exec[ \t]+([^;\]\n]+)")
_STRING_LITERAL_RE = re.compile(r"""'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`[^`]*`""")
# A triple-quoted literal may span lines, and is tried first, so a call written inside a
# docstring is masked as data rather than read as code.
_QUOTED_LITERAL_RE = re.compile(
    r"\"\"\"(?s:.*?)\"\"\"|'''(?s:.*?)'''|'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\""
)
# In a Python program a `#` comment is blanked as well, whichever of comment or literal
# starts first, so a quote written in a comment does not open a literal.
_PYTHON_HEAD_RE = re.compile(r"^(?:python|pypy)")
_PYTHON_MASKED_RE = re.compile(r"#[^\n]*|" + _QUOTED_LITERAL_RE.pattern)
# A string literal with an optional prefix (f"…", r'…', b"…", rf"…"): group tdq/tsq/sq/dq
# is the text between the quotes. A triple-quoted literal may span lines, and is tried
# first, so `"""x"""` is not read as the empty literal `""`. An f-string's {…}
# placeholders stay in the text.
_PREFIXED_LITERAL_RE = re.compile(
    r"(?<![\w$])(?:[rRbBuUfF]{1,2})?(?:\"\"\"(?P<tdq>(?s:.*?))\"\"\"|'''(?P<tsq>(?s:.*?))'''"
    r"|'(?P<sq>(?:[^'\\\n]|\\.)*)'|\"(?P<dq>(?:[^\"\\\n]|\\.)*)\")"
    r"|(?<![\w$])`(?P<bq>[^`]*)`"
)
# Interpreters whose bare backtick span runs a shell command; in JavaScript a backtick is
# a template string, which runs only when tagged with `$` (Bun.$`…`, zx's $`…`).
_BACKTICK_EXEC_HEAD_RE = re.compile(r"^(?:perl[0-9.]*|ruby[0-9.]*|php[0-9.]*(?:-cgi)?)$")
_DIRECT_CALL_MAX_DEPTH = 2


@functools.lru_cache(maxsize=64)
def _mask_string_literals(code: str, head: str = "") -> str:
    """`code` with the content of every '…' and "…" literal blanked, same length, so call
    names and backticks written INSIDE a string (data) are not read as code. For a
    Python `head`, `#` comments are blanked whole."""
    pattern = _PYTHON_MASKED_RE if _PYTHON_HEAD_RE.match(head) else _QUOTED_LITERAL_RE
    return pattern.sub(_blank_literal, code)


def _blank_literal(m: re.Match) -> str:
    """A masked literal keeps its quote characters; a masked comment is all blanks."""
    text = m.group(0)
    if text.startswith("#"):
        return " " * len(text)
    return text[0] + " " * (len(text) - 2) + text[-1]


def _skip(pattern: str, text: str, i: int) -> int:
    """Offset past `pattern` matched at text[i], or i when it does not match: an int
    where `re.match(...).end()` would be Optional, for patterns that can match the empty
    string."""
    m = re.compile(pattern).match(text, i)
    return m.end() if m else i


# How many readings one expression may produce when names in it reach several values.
_MAX_READINGS = 16
# How many names deep a value is followed (`a = b + "x"; b = "gh "`); a name whose
# value is itself built from the name stops here.
_MAX_NAME_DEPTH = 3


def _with_each(readings: list, options: list) -> list:
    """Each reading extended by each option, at most _MAX_READINGS of them."""
    return [r + [o] for r in readings for o in options][:_MAX_READINGS]


def _literal_list_at(text: str, i: int, head: str = "", _depth: int = 0) -> tuple[list[list[str]], int] | None:
    """The readings of a `[…]` list, `(…)` tuple or R `c(…)` vector starting at text[i],
    each the elements it spells, and the offset after it. A bare-name element reads as
    each literal string its reaching assignments give it (`g = "gh"; [g, "pr", …]`). A
    spread element (`*a`, `...a`) and, in an R `c(…)`, a name whose value is a vector
    splice in that list's elements. A Python call's argument list read as a `*args`
    tuple skips the arguments the named parameters take, and a Python call's keyword
    arguments are never elements. A later non-literal element (`str(pr)`, a variable
    with no literal value) reads as `X`; a leading one reads as `gh` when `"pr"` follows
    it, and otherwise the list is not read. None also when an element is neither."""
    opener = re.match(r"\[|c?\(", text[i:])
    if not opener:
        return None
    reach = _python_reaching(text) if opener.group() == "(" and _PYTHON_HEAD_RE.match(head) else None
    skip = reach.vararg_skip.get(i, 0) if reach else 0
    closer = "]" if opener.group() == "[" else ")"
    readings: list[list[str]] = [[]]
    count, i = 0, i + opener.end()
    while True:
        i = _skip(r"\s*", text, i)
        if text[i:i + 1] == closer:
            return [r[skip:] for r in readings], i + 1
        keyword = re.match(r"\*\*|[A-Za-z_]\w*\s*=(?!=)", text[i:]) if reach else None
        if keyword:                                      # a keyword argument: not in `*args`
            end = _expression_end(_mask_string_literals(text), i + keyword.end())
            i = _skip(r"\s*,?", text, end)
            continue
        lit = _STRING_LITERAL_RE.match(text, i)
        spread = None if lit else re.match(r"\*(?!\*)|\.\.\.", text[i:])
        at = i + spread.end() if spread else i
        named = None if lit or _depth >= _MAX_NAME_DEPTH else _named_values_at(head, text, at)
        lists = _named_lists(head, text, named[0], _depth) if named and (spread or opener.group() == "c(") else []
        values = _named_strings(head, text, named[0], _depth + 1) if named and not lists else []
        if lit:
            readings, end = _with_each(readings, [lit.group(0)[1:-1]]), lit.end()
        elif lists:
            readings = [r + items for r in readings for items in lists][:_MAX_READINGS]
            end = named[1] if named else at
        elif named and values:
            readings, end = _with_each(readings, values), named[1]
        else:
            end = _operand_end(text, i)
            if end < 0 or (not count and not _leads_a_gh_pr_argv(head, text, i, end)):
                return None
            readings = _with_each(readings, ["X" if count else "gh"])
        count += 1
        i = _skip(r"\s*,?", text, end)


def _named_strings(head: str, code: str, offsets: list[int], _depth: int = 0) -> list[str]:
    """The string texts the values at these offsets spell, in order, without repeats."""
    out: list[str] = []
    for off in offsets:
        expr = _string_expr_at(code, off, head, _depth)
        out += [text for text in (expr[0] if expr else []) if text not in out]
    return out[:_MAX_READINGS]


def _named_lists(head: str, code: str, offsets: list[int], _depth: int = 0) -> list[list[str]]:
    """The element readings of the list values at these offsets, without repeats."""
    out: list[list[str]] = []
    for off in offsets:
        lst = _list_expr_at(code, off, head, _depth + 1)
        out += [items for items in (lst[0] if lst else []) if items not in out]
    return out[:_MAX_READINGS]


def _copied_values_at(head: str, code: str, i: int) -> tuple[list[int], int] | None:
    """For a Python `list(name)` / `tuple(name)` at code[i], the values `name` holds and
    the offset past the call: the copy holds the same elements."""
    call = re.match(r"(?:list|tuple)\s*\(\s*", code[i:]) if _PYTHON_HEAD_RE.match(head) else None
    named = _named_values_at(head, code, i + call.end()) if call else None
    close = re.match(r"\s*\)", code[named[1]:]) if named else None
    return (named[0], named[1] + close.end()) if named and close else None


def _list_expr_at(code: str, i: int, head: str = "", _depth: int = 0) -> tuple[list[list[str]], int] | None:
    """The readings of a list expression starting at code[i]: list literals, names whose
    values are lists (also as Python `list(name)` / `tuple(name)`) and calls to a Python
    function that returns one, joined with `+` (`A + ["5"]`). None when no operand is a
    list."""
    readings: list[list[str]] = [[]]
    while True:
        i = _skip(r"\s*", code, i)
        lst = _literal_list_at(code, i, head, _depth)
        named = None if lst or _depth >= _MAX_NAME_DEPTH else (
            _named_values_at(head, code, i, _NAME_OPERAND_RE) or _returned_values_at(head, code, i)
            or _member_values_at(head, code, i) or _copied_values_at(head, code, i)
        )
        lists = _named_lists(head, code, named[0], _depth) if named else []
        if lst:
            options, i = lst[0], lst[1]
        elif lists and named:
            options, i = lists, named[1]
        else:
            return None
        readings = [r + items for r in readings for items in options][:_MAX_READINGS]
        plus = re.match(r"\s*\+\s*", code[i:])
        if not plus:
            return readings, i
        i += plus.end()


# gh or hub as a word, in any case: `GH`, `/opt/homebrew/bin/gh`, `which("hub")`.
_GH_WORD_RE = re.compile(r"(?<![A-Za-z0-9])(?:gh|hub)(?![A-Za-z0-9])", re.IGNORECASE)


def _leads_a_gh_pr_argv(head: str, text: str, start: int, end: int) -> bool:
    """True iff the unresolved leading list element text[start:end] is gh resolved at
    run time (`shutil.which("gh")`, `os.environ.get("GH", "gh")`, a name `gh`): the
    next element is the literal `"pr"`, and the element's text names gh or hub, or,
    for a bare name, the name or a value assigned to it does. The rest of the argv is
    then judged as `gh pr …` is."""
    nxt = _STRING_LITERAL_RE.match(text, _skip(r"\s*,\s*", text, end))
    if not nxt or nxt.group(0)[1:-1] != "pr":
        return False
    source = text[start:end]
    named = _named_values_at(head, text, start)
    for offset in named[0] if named else []:
        source += " " + text[offset:].partition("\n")[0].partition(";")[0]
    return bool(_GH_WORD_RE.search(source))


_OBJECT_ARGV_KEY_RE = re.compile(r"(?<![\w$])[\"']?(?:args|cmd)[\"']?\s*:\s*")


def _object_argv_at(code: str, i: int, head: str = "") -> list[list[str]]:
    """The readings of the literal `args:` / `cmd:` list at the top level of the object
    literal starting at code[i], or [] when there is none or the object's bounds are
    unclear. Braces and keys inside string literals do not count."""
    if code[i:i + 1] != "{":
        return []
    masked = _mask_string_literals(code)
    depth, j = 0, i
    while j < len(masked):
        if masked[j] in "{[(":
            depth += 1
        elif masked[j] in "}])":
            depth -= 1
            if depth == 0:
                break
        elif depth == 1:
            key = _OBJECT_ARGV_KEY_RE.match(masked, j)
            if key:
                lst = _literal_list_at(code, key.end(), head)
                return lst[0] if lst else []
        j += 1
    return []


def _literal_body(m: re.Match) -> str:
    """The text a string literal spells, with backslash escapes resolved (`\\"` -> `"`)."""
    body = next(g for g in m.group("tdq", "tsq", "sq", "dq", "bq") if g is not None)
    return re.sub(r"\\(.)", r"\1", body)


def _balanced_end(code: str, i: int) -> int:
    """Offset just past the bracket group opening at code[i] (string contents masked),
    or -1 when it does not close."""
    masked, depth = _mask_string_literals(code), 0
    for j in range(i, len(masked)):
        if masked[j] in "([{":
            depth += 1
        elif masked[j] in ")]}":
            depth -= 1
            if depth == 0:
                return j + 1
    return -1


_OPERAND_RE = re.compile(r"[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*|\d+")
_OPERAND_ATTR_RE = re.compile(r"\s*\.\s*[A-Za-z_$][\w$]*")


def _operand_end(code: str, i: int) -> int:
    """Offset past one non-literal operand at code[i] (a name, attribute chain or number,
    followed by any calls, subscripts and attributes: `sys.argv[1]`, `pr["number"]`,
    `str(n).strip()`), or -1 when there is none or a bracket does not close."""
    operand = _OPERAND_RE.match(code, i)
    if not operand:
        return -1
    i = operand.end()
    while True:
        if code[i:i + 1] in ("(", "["):
            i = _balanced_end(code, i)
            if i < 0:
                return -1
            continue
        attr = _OPERAND_ATTR_RE.match(code, i)
        if not attr:
            return i
        i = attr.end()


def _string_expr_at(code: str, i: int, head: str = "", _depth: int = 0) -> tuple[list[str], int] | None:
    """The readings of a string-valued argument expression starting at code[i], each the
    text it spells: string literals (any prefix; an f-string keeps its {…}), joined with
    `+` to each other or to other operands, `% …` applied to a literal (the literal's
    text), `shlex.split(<literal>)`, and `"<sep>".join([…])` (one reading per reading of
    the list). A name operand reads as each literal value its reaching assignments give
    it (`base = "gh pr "; base + "merge 5"`), a call to a function the Python program
    defines as each value it returns, and any other operand as `X`. A
    `.format(…)` ends the expression after its literal, which keeps the literal's text.
    None when no string literal or literal-valued name takes part."""
    readings, saw_literal = [""], False
    while True:
        i = _skip(r"\s*", code, i)
        split = re.match(r"shlex\s*\.\s*split\s*\(\s*", code[i:])
        lit = _PREFIXED_LITERAL_RE.match(code, i + (split.end() if split else 0))
        if lit:
            text, i, saw_literal = _literal_body(lit), lit.end(), True
            if split:
                close = re.match(r"\s*\)", code[i:])
                if not close:
                    return None
                i += close.end()
            texts = [text]
            joiner = re.match(r"\s*\.\s*join\s*\(\s*", code[i:])
            if joiner:
                lst = _literal_list_at(code, i + joiner.end(), head)
                close = re.match(r"\s*\)", code[lst[1]:]) if lst else None
                if not lst or not close:
                    return None
                texts, i = [text.join(items) for items in lst[0]], lst[1] + close.end()
            readings = [r + t for r in readings for t in texts][:_MAX_READINGS]
        else:
            named = (
                _named_values_at(head, code, i, _NAME_OPERAND_RE) or _returned_values_at(head, code, i)
                or _member_values_at(head, code, i)
            ) if _depth < _MAX_NAME_DEPTH else None
            values = _named_strings(head, code, named[0], _depth + 1) if named else []
            if named and values:
                readings = [r + v for r in readings for v in values][:_MAX_READINGS]
                i, saw_literal = named[1], True
            else:
                end = _operand_end(code, i)
                if end < 0:
                    break
                i = end
                readings = [r + "X" for r in readings]
        percent = re.match(r"\s*%\s*", code[i:])
        if percent and saw_literal:
            nxt = i + percent.end()
            lit2 = _PREFIXED_LITERAL_RE.match(code, nxt)
            end = lit2.end() if lit2 else (
                _balanced_end(code, nxt) if code[nxt:nxt + 1] in "([{" else _operand_end(code, nxt)
            )
            if end > 0:
                i = end
            break
        plus = re.match(r"\s*\+\s*", code[i:])
        if not plus:
            break
        i += plus.end()
    if not saw_literal:
        return None
    return readings, i


def _first_argument_command(code: str, pos: int, head: str = "") -> str | None:
    """The first of `_argument_commands`, or None."""
    cmds = _argument_commands(code, pos, head=head)
    return cmds[0] if cmds else None


# A call argument or list element that is a bare variable name (`run(cmd)`,
# `system($c)`, `[g, "pr"]`), up to the comma or bracket that ends it.
_BARE_NAME_ARG_RE = re.compile(r"(?P<name>[$@]?[A-Za-z_]\w*)(?=\s*[,)\]])")
# A name operand of a `+` expression: followed by `+`, the end of the argument, or the
# end of its statement.
_NAME_OPERAND_RE = re.compile(r"(?P<name>[$@]?[A-Za-z_]\w*)(?=[ \t]*(?:[+,)\]};\n]|$))")


# `name = value`, outside string literals; `==`, `=~` and `=>` are not assignments.
_ASSIGNMENT_RE = re.compile(r"(?<![\w$@.])(?P<name>[$@]?[A-Za-z_]\w*)\s*=(?![=~>])\s*")


# Literal forms that may hold a comment opener, per family: a JS template (a URL in
# it), a lua long string, ruby %w/%q/%Q/%i lists, and perl q/qq/qw/qr/m and s/tr/y
# forms. Each `(?P<d…>)` group is a delimiter its own literal closes on.
_PERL_LITERALS = (
    r"(?<![\w$@%&])(?:qq|qw|qr|q|m)\s*(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|<[^>]*>"
    r"|(?P<dq>[^\w\s(\[{<])(?:(?!(?P=dq)).)*(?P=dq))"
    r"|(?<![\w$@%&])(?:s|tr|y)\s*(?P<ds>[^\w\s(\[{<])(?:(?!(?P=ds)).)*(?P=ds)(?:(?!(?P=ds)).)*(?P=ds)[a-z]*"
)
_RUBY_LITERALS = (
    r"%[wWqQiI](?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|<[^>]*>"
    r"|(?P<dr>[^\w\s(\[{<])(?:(?!(?P=dr)).)*(?P=dr))"
)
# Per head family, its own literal forms and its comments. One leftmost match decides,
# so a literal that starts first stays a literal and a comment that starts first is a
# comment. The assignment index blanks both, so a commented-out assignment is not a
# value; the call scan does not use this mask. Python's `#` comments are already
# blanked by _mask_string_literals for a Python head.
_INDEX_COMMENT_RES = tuple(
    (re.compile(head_re), re.compile("|".join(part for part in (literals, comments, _QUOTED_LITERAL_RE.pattern) if part)))
    for head_re, literals, comments in (
        (r"^(?:node|nodejs|bun|deno)", r"`(?:[^`\\]|\\.)*`", r"/\*(?s:.*?)\*/|//[^\n]*"),
        (r"^php", "", r"/\*(?s:.*?)\*/|//[^\n]*|#(?!\[)[^\n]*"),
        (r"^perl", _PERL_LITERALS, r"(?:(?m:^)|(?<=[\s;]))#(?!\{)[^\n]*"),
        (r"^ruby", _RUBY_LITERALS, r"(?:(?m:^)|(?<=[\s;]))#(?!\{)[^\n]*"),
        (r"^(?:R$|Rscript|tclsh|wish)", "", r"(?:(?m:^)|(?<=[\s;]))#(?!\{)[^\n]*"),
        (r"^(?:lua|luajit)", r"\[(?P<ll>=*)\[(?s:.*?)\](?P=ll)\]", r"--\[(?P<lc>=*)\[(?s:.*?)\](?P=lc)\]|--[^\n]*"),
        (r"^osascript", "", r"\(\*(?s:.*?)\*\)|--[^\n]*"),
    )
)
# A `/` earlier on the line may open a regex (`=~ / #/`, `split / #/`), which this
# view does not delimit, so a `#` after it is not taken as a comment: the line stays
# readable.
_SLASH_GUARDED_RE = re.compile(r"^(?:perl|ruby)")


def _blank_kept_lines(m: re.Match) -> str:
    """A masked comment is blanks with its line breaks kept; a literal keeps its quotes."""
    text = m.group(0)
    if text[0] in "\"'":
        return _blank_literal(m)
    return re.sub(r"[^\n]", " ", text)


@functools.lru_cache(maxsize=64)
def _index_view(code: str, head: str) -> str:
    """`code` with string literals and, for a known head, its own literal forms and its
    comments blanked, same length: the text the assignment index and the scope finder
    read."""
    guarded = bool(_SLASH_GUARDED_RE.match(head))

    def blank(m: re.Match) -> str:
        text = m.group(0)
        if guarded and text.startswith("#") and "/" in code[code.rfind("\n", 0, m.start()) + 1:m.start()]:
            return text
        return _blank_kept_lines(m)

    for head_re, pattern in _INDEX_COMMENT_RES:
        if head_re.match(head):
            return pattern.sub(blank, code)
    return _mask_string_literals(code, head)


# R assigns with `<-` and, to the enclosing environment, `<<-`; TypeScript annotates a
# declaration's type before its `=`.
_R_ASSIGNMENT_RE = re.compile(r"(?<![\w.])(?P<name>[A-Za-z_.][\w.]*)\s*(?P<op><<-|<-|=(?![=~>]))\s*")
_TS_ASSIGNMENT_RE = re.compile(
    r"(?<![\w$@.])(?P<name>[A-Za-z_$][\w$]*)\s*(?::\s*[\w$.]+(?:<[^<>=;\n]*>)?(?:\[\])*\s*)?=(?![=>])\s*"
)
_R_HEAD_RE = re.compile(r"^(?:R$|Rscript)")


def _assignment_re(head: str) -> re.Pattern:
    """The assignment pattern for a head's language."""
    if _R_HEAD_RE.match(head):
        return _R_ASSIGNMENT_RE
    return _TS_ASSIGNMENT_RE if _JS_HEAD_RE.match(head) else _ASSIGNMENT_RE


@functools.lru_cache(maxsize=64)
def _enclosing_assignments(code: str, head: str) -> frozenset:
    """The offsets of R `<<-` assignments, which assign in the enclosing environment."""
    if not _R_HEAD_RE.match(head):
        return frozenset()
    return frozenset(m.start() for m in _R_ASSIGNMENT_RE.finditer(_index_view(code, head)) if m.group("op") == "<<-")


@functools.lru_cache(maxsize=64)
def _assignments(code: str, head: str = "") -> dict[str, tuple[list[int], list[int]]]:
    """Per assigned name, the offsets of its assignments and of their values, in order,
    found once per program. Assignments inside string literals or comments do not count."""
    found: dict[str, tuple[list[int], list[int]]] = {}
    for m in _assignment_re(head).finditer(_index_view(code, head)):
        starts, values = found.setdefault(m.group("name"), ([], []))
        starts.append(m.start())
        values.append(m.end())
    return found


# A binding whose value cannot be read: a parameter, a loop or `with` target, an import,
# a def or class name, a tuple target.
_OPAQUE = -1
_PY_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_PY_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_PY_TRY = tuple(t for t in (getattr(ast, "Try", None), getattr(ast, "TryStar", None)) if t)
_PY_MATCH = getattr(ast, "Match", ())


def _env_union(*envs: dict) -> dict:
    """Per name, the union of its definitions across environments."""
    out: dict = {}
    for env in envs:
        for name, defs in env.items():
            out[name] = out.get(name, frozenset()) | defs
    return out


class _PyScope:
    """A Python scope: the module, a def or a lambda (a class body is processed inside
    the scope that encloses it)."""

    def __init__(self, node, parent):
        self.node, self.parent = node, parent
        self.defs: dict = {}            # name -> every definition anywhere in the scope
        self.locals: set = set()
        self.free_memo: dict = {}


class _PyReach:
    """For a parsed Python program, the definitions that may reach each name it reads,
    keyed by the read's character offset: reaching definitions per scope, in one forward
    pass per scope. A literal assignment replaces a name's definitions, an extension
    (`+=`, `x = x + …`, `.append`) keeps them, branches and loops take the union, and
    an unreadable binding is _OPAQUE. A name free in a def reads the enclosing scope at
    the def's direct call sites (every definition in that scope when it has none), and
    a `global` or `nonlocal` write joins the caller at those call sites. A parameter
    reads the argument passed for it at every call of its function by name (its default
    where a call omits it) and each element of a literal list `map(f, [...])` passes it,
    a `for` target over a literal list reads each element, and `returns` maps each call
    by name to the values its function returns."""

    def __init__(self, code: str, tree: ast.Module):
        self.code = code
        self.starts = [0] + [m.end() for m in re.finditer(r"\r\n|\r|\n", code)]
        self.uses: dict = {}
        self.callsites: dict = {}      # (id(scope), function name) -> [environment]
        self.pending: list = []        # (def node, enclosing scope)
        self.lambdas: list = []        # (lambda node, enclosing scope)
        self.functions: dict = {}      # name -> def nodes
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions.setdefault(node.name, []).append(node)
        self.calls: dict = {}          # function name -> call nodes, anywhere in the program
        self.returns: dict = {}        # call offset -> offsets of its function's return values
        self.mapped: dict = {}         # function name -> literal elements `map(f, [...])` passes it
        self.vararg_skip: dict = {}    # call `(` offset -> named arguments before its `*args`
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "map"
                and len(node.args) == 2 and isinstance(node.args[0], ast.Name) and node.args[0].id in self.functions
                and self._literal_elements(node.args[1]) is not None
            ):
                self.mapped.setdefault(node.args[0].id, []).extend(self._literal_elements(node.args[1]) or [])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in self.functions:
                self.calls.setdefault(node.func.id, []).append(node)
                self.returns[self._offset(node)] = frozenset(
                    self._offset(r.value) for fn in self.functions[node.func.id]
                    for r in self._own_nodes(fn.body) if isinstance(r, ast.Return) and r.value is not None
                )
        self.outer_writes = {id(fn): self._outer_writes(fn) for fns in self.functions.values() for fn in fns}
        module = _PyScope(tree, None)
        self._block(tree.body, {}, module, module)
        while self.pending or self.lambdas:
            if self.pending:
                fn, outer = self.pending.pop(0)
                self._function(fn, outer)
            else:
                lam, outer = self.lambdas.pop(0)
                self._lambda(lam, outer)

    @staticmethod
    def _literal_elements(node) -> list | None:
        """The elements of a list or tuple literal, or None (a `*` element reads nothing)."""
        return list(node.elts) if isinstance(node, (ast.List, ast.Tuple)) else None

    # -- offsets ------------------------------------------------------------------------
    def _offset(self, node) -> int:
        start = self.starts[node.lineno - 1]
        line = self.code[start:start + node.col_offset]
        if line.isascii():
            return start + node.col_offset
        return start + len(line.encode()[:node.col_offset].decode("utf-8", "ignore"))

    # -- what a scope binds ---------------------------------------------------------------
    @staticmethod
    def _target_names(target) -> list:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            return [n for elt in target.elts for n in _PyReach._target_names(elt)]
        if isinstance(target, ast.Starred):
            return _PyReach._target_names(target.value)
        return []

    @staticmethod
    def _own_nodes(stmts):
        """Every node under these statements outside nested defs, lambdas and classes
        (whose names are yielded, not their bodies)."""
        stack = list(stmts)
        while stack:
            node = stack.pop()
            yield node
            if isinstance(node, _PY_SCOPES):
                continue
            stack.extend(ast.iter_child_nodes(node))

    def _bindings(self, stmts) -> dict:
        """name -> the definitions these statements give it, outside nested scopes."""
        out: dict = {}
        for node in self._own_nodes(stmts):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    for name in self._target_names(t):
                        out.setdefault(name, set()).add(self._offset(node.value) if isinstance(t, ast.Name) else _OPAQUE)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                for name in self._target_names(node.target):
                    out.setdefault(name, set()).add(self._offset(node.value))
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                for name in self._target_names(node.target):
                    out.setdefault(name, set()).add(_OPAQUE)
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                for name in self._target_names(node.optional_vars):
                    out.setdefault(name, set()).add(_OPAQUE)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    out.setdefault((alias.asname or alias.name).split(".")[0], set()).add(_OPAQUE)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) or (
                isinstance(node, ast.ExceptHandler) and node.name
            ):
                out.setdefault(node.name, set()).add(_OPAQUE)
            elif isinstance(node, ast.NamedExpr):
                out.setdefault(node.target.id, set()).add(self._offset(node.value))
        return out

    def _outer_writes(self, fn) -> dict:
        """For a def's `global` / `nonlocal` names, the definitions it writes to them."""
        declared = {n for node in self._own_nodes(fn.body) if isinstance(node, (ast.Global, ast.Nonlocal)) for n in node.names}
        written = self._bindings(fn.body)
        return {name: frozenset(written.get(name, ())) for name in declared}

    # -- lookups --------------------------------------------------------------------------
    def _lookup(self, name: str, env: dict, scope: _PyScope) -> frozenset:
        if name in env:
            return env[name]
        if scope.parent is None or name in scope.locals:
            return frozenset()
        return self._free(scope, name)

    def _free(self, scope: _PyScope, name: str) -> frozenset:
        """A name free in a def or lambda: the enclosing scope at the def's direct call
        sites, or every definition of it there when it has none."""
        if name in scope.free_memo:
            return scope.free_memo[name]
        scope.free_memo[name] = frozenset()             # a cycle reads as nothing
        outer = scope.parent
        envs = [] if isinstance(scope.node, ast.Lambda) else self.callsites.get((id(outer), scope.node.name), [])
        if envs:
            found = frozenset().union(*(self._lookup(name, env, outer) for env in envs))
        else:
            found = frozenset(outer.defs.get(name, ()))
            if not found and outer.parent is not None and name not in outer.locals:
                found = self._free(outer, name)
        scope.free_memo[name] = found
        return found

    # -- expressions ----------------------------------------------------------------------
    def _read(self, node, env: dict, scope: _PyScope, opaque=frozenset()) -> tuple[list, list]:
        """Record the definitions reaching each name read under node; return the names of
        the functions it calls directly, and the walrus bindings it makes."""
        called, stack = [], [(node, opaque)]
        walrus = []
        while stack:
            n, bound = stack.pop()
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                self.uses[self._offset(n)] = frozenset({_OPAQUE}) if n.id in bound else self._lookup(n.id, env, scope)
                continue
            if isinstance(n, ast.Lambda):
                stack.extend((d, bound) for d in n.args.defaults + [d for d in n.args.kw_defaults if d is not None])
                self.lambdas.append((n, scope))
                continue
            if isinstance(n, _PY_COMPREHENSIONS):
                names = {x for g in n.generators for x in self._target_names(g.target)}
                stack.extend((c, bound | names) for c in ast.iter_child_nodes(n))
                continue
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                called.append(n.func.id)
            if isinstance(n, ast.NamedExpr):
                walrus.append((n.target.id, self._offset(n.value)))
            stack.extend((c, bound) for c in ast.iter_child_nodes(n))
        for name in called:
            if name in self.functions:
                self.callsites.setdefault((id(scope), name), []).append(dict(env))
        return called, walrus

    def _effects(self, read, env: dict) -> dict:
        """Apply a statement's walrus bindings and its callees' global/nonlocal writes."""
        called, walrus = read
        for name, off in walrus:
            env[name] = frozenset({off})
        for callee in called:
            for fn in self.functions.get(callee, []):
                for name, defs in self.outer_writes[id(fn)].items():
                    env[name] = env.get(name, frozenset()) | defs
        return env

    # -- statements -----------------------------------------------------------------------
    def _block(self, stmts, env: dict, scope: _PyScope, defs_scope: _PyScope) -> dict:
        env = dict(env)
        for st in stmts:
            env = self._stmt(st, env, scope, defs_scope)
        return env

    def _assign_defs(self, defs_scope: _PyScope, name: str, defs) -> None:
        defs_scope.defs.setdefault(name, set()).update(defs)

    def _stmt(self, st, env: dict, scope: _PyScope, defs_scope: _PyScope) -> dict:
        if isinstance(st, ast.Assign):
            read = self._read(st.value, env, scope)
            for t in st.targets:
                if not isinstance(t, ast.Name):
                    self._read(t, env, scope)
            value, rhs = self._offset(st.value), st.value
            extended = (
                rhs.left.id
                if isinstance(rhs, ast.BinOp) and isinstance(rhs.op, ast.Add) and isinstance(rhs.left, ast.Name)
                else None
            )
            for t in st.targets:
                for name in self._target_names(t):
                    if extended == name and isinstance(t, ast.Name):
                        continue                         # `x = x + …` extends x
                    defs = frozenset({value}) if isinstance(t, ast.Name) else frozenset({_OPAQUE})
                    env[name] = defs
                    self._assign_defs(defs_scope, name, defs)
            return self._effects(read, env)
        if isinstance(st, ast.AnnAssign):
            read = self._read(st.value, env, scope) if st.value is not None else ([], [])
            if st.value is not None and isinstance(st.target, ast.Name):
                env[st.target.id] = frozenset({self._offset(st.value)})
                self._assign_defs(defs_scope, st.target.id, env[st.target.id])
            return self._effects(read, env)
        if isinstance(st, ast.AugAssign):
            return self._effects(self._read(st, env, scope), env)   # extends: definitions kept
        if isinstance(st, (ast.Import, ast.ImportFrom)):
            for alias in st.names:
                name = (alias.asname or alias.name).split(".")[0]
                env[name] = frozenset({_OPAQUE})
                self._assign_defs(defs_scope, name, env[name])
            return env
        if isinstance(st, ast.Delete):
            for t in st.targets:
                for name in self._target_names(t):
                    env[name] = frozenset()
            return env
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in st.decorator_list + st.args.defaults + [d for d in st.args.kw_defaults if d is not None]:
                self._read(d, env, scope)
            env[st.name] = frozenset({_OPAQUE})
            self._assign_defs(defs_scope, st.name, env[st.name])
            self.pending.append((st, scope))
            return env
        if isinstance(st, ast.ClassDef):
            for d in st.decorator_list + st.bases + [k.value for k in st.keywords]:
                self._read(d, env, scope)
            self._block(st.body, env, scope, _PyScope(st, scope))   # class attributes stay in the class
            env[st.name] = frozenset({_OPAQUE})
            self._assign_defs(defs_scope, st.name, env[st.name])
            return env
        if isinstance(st, ast.If):
            self._effects(self._read(st.test, env, scope), env)
            return _env_union(self._block(st.body, env, scope, defs_scope), self._block(st.orelse, env, scope, defs_scope))
        if isinstance(st, (ast.For, ast.AsyncFor, ast.While)):
            head = st.test if isinstance(st, ast.While) else st.iter
            self._effects(self._read(head, env, scope), env)
            carried = {name: frozenset(defs) for name, defs in self._bindings(st.body).items()}
            start = _env_union(env, carried)
            if not isinstance(st, ast.While):
                elements = self._literal_elements(st.iter) if isinstance(st.target, ast.Name) else None
                for name in self._target_names(st.target):
                    start[name] = frozenset(self._offset(e) for e in elements) if elements else frozenset({_OPAQUE})
                    self._assign_defs(defs_scope, name, start[name])
            end = _env_union(env, self._block(st.body, start, scope, defs_scope))
            return _env_union(end, self._block(st.orelse, end, scope, defs_scope)) if st.orelse else end
        if isinstance(st, (ast.With, ast.AsyncWith)):
            for item in st.items:
                self._effects(self._read(item.context_expr, env, scope), env)
                for name in self._target_names(item.optional_vars) if item.optional_vars is not None else []:
                    env[name] = frozenset({_OPAQUE})
                    self._assign_defs(defs_scope, name, env[name])
            return self._block(st.body, env, scope, defs_scope)
        if _PY_TRY and isinstance(st, _PY_TRY):
            body = self._block(st.body, env, scope, defs_scope)
            ends = [body]
            for handler in st.handlers:
                start = _env_union(env, body)
                if handler.name:
                    start[handler.name] = frozenset({_OPAQUE})
                ends.append(self._block(handler.body, start, scope, defs_scope))
            if st.orelse:
                ends.append(self._block(st.orelse, body, scope, defs_scope))
            joined = _env_union(env, *ends)
            return self._block(st.finalbody, joined, scope, defs_scope) if st.finalbody else joined
        if _PY_MATCH and isinstance(st, _PY_MATCH):
            self._effects(self._read(st.subject, env, scope), env)
            ends = [env]
            for case in st.cases:
                start = dict(env)
                for node in ast.walk(case.pattern):
                    for name in [getattr(node, "name", None)] + list(getattr(node, "names", []) or []):
                        if isinstance(name, str):
                            start[name] = frozenset({_OPAQUE})
                if case.guard is not None:
                    self._read(case.guard, start, scope)
                ends.append(self._block(case.body, start, scope, defs_scope))
            return _env_union(*ends)
        if isinstance(st, (ast.Global, ast.Nonlocal, ast.Pass, ast.Break, ast.Continue)):
            return env
        return self._effects(self._read(st, env, scope), env)

    # -- defs and lambdas -----------------------------------------------------------------
    @staticmethod
    def _params(args) -> list:
        names = [a.arg for a in getattr(args, "posonlyargs", []) + args.args + args.kwonlyargs]
        return names + [a.arg for a in (args.vararg, args.kwarg) if a is not None]

    def _arguments(self, fn) -> dict:
        """Per parameter of `fn`, the offsets of what its calls pass for it: the argument
        by position or keyword, else the parameter's default; _OPAQUE for a call that
        omits it with no default or passes `*args`/`**kwargs`, and for a `*`/`**`
        parameter. A function with no call by name gives its parameters nothing."""
        args, calls = fn.args, self.calls.get(fn.name, [])     # and `map(fn, [...])` elements
        positional = getattr(args, "posonlyargs", []) + args.args
        defaults = dict(zip([a.arg for a in positional][len(positional) - len(args.defaults):], args.defaults))
        defaults.update((a.arg, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None)
        out: dict = {name: set() for name in self._params(args)}
        for name in [a.arg for a in (args.vararg, args.kwarg) if a is not None]:
            out[name] = {_OPAQUE}
        if args.vararg is not None:
            # `def f(x, *a)`: a call's argument list past the named ones is the tuple `a`
            # holds; the list reader skips the first len(positional) of it.
            parens = {self._call_parens(call) for call in calls}
            self.vararg_skip.update((paren, len(positional)) for paren in parens)
            out[args.vararg.arg] = parens or {_OPAQUE}
        invocations = [(call.args, call.keywords) for call in calls]
        invocations += [([element], []) for element in self.mapped.get(fn.name, [])]
        for given_args, given_keywords in invocations:
            unpacked = any(isinstance(a, ast.Starred) for a in given_args) or any(k.arg is None for k in given_keywords)
            keywords = {k.arg: k.value for k in given_keywords}
            for k, a in enumerate(positional + args.kwonlyargs):
                given = given_args[k] if k < len(positional) and k < len(given_args) else keywords.get(a.arg, defaults.get(a.arg))
                out[a.arg].add(_OPAQUE if unpacked or given is None else self._offset(given))
        return {name: frozenset(defs) for name, defs in out.items()}

    def _call_parens(self, call) -> int:
        """The offset of the `(` that opens a call's argument list."""
        k = self._offset(call.func) + len(call.func.id)
        return self.code.index("(", k)

    def _function(self, fn, outer: _PyScope) -> None:
        scope = _PyScope(fn, outer)
        declared = set(self.outer_writes[id(fn)])
        params = self._params(fn.args)
        scope.locals = (set(params) | set(self._bindings(fn.body))) - declared
        env = self._arguments(fn)
        self._block(fn.body, env, scope, scope)

    def _lambda(self, lam, outer: _PyScope) -> None:
        scope = _PyScope(lam, outer)
        scope.locals = set(self._params(lam.args))
        self._read(lam.body, {name: frozenset({_OPAQUE}) for name in scope.locals}, scope)


@functools.lru_cache(maxsize=64)
def _python_reaching(code: str) -> "_PyReach | None":
    """A Python program's reaching definitions and call returns (see _PyReach), or None
    when it does not parse. Warnings the parse raises about the program (an invalid
    escape in its strings) are the program's, not the hook's, so none reaches stderr."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(code)
    except (SyntaxError, ValueError, MemoryError):
        return None
    return _PyReach(code, tree)


def _returned_values_at(head: str, code: str, i: int) -> tuple[list[int], int] | None:
    """For a call at code[i] to a function the program defines, (the offsets of the
    values it returns, offset past the call); None otherwise. A ruby method called with
    no arguments needs no parentheses (`system(cmd)`)."""
    if _PYTHON_HEAD_RE.match(head):
        reach = _python_reaching(code)
        values = sorted(reach.returns.get(i, ())) if reach else []
    else:
        callee = re.match(r"[A-Za-z_$][\w$.]*[?!]?(?=\s*\()" + (r"|[A-Za-z_]\w*[?!]?" if head.startswith("ruby") else ""), code[i:])
        fn = _named_functions(code, head).get(callee.group()) if callee else None
        values = sorted(fn.returns) if fn else []
        if callee and values and code[callee.end() + i:].lstrip()[:1] != "(":
            return values, i + callee.end()
    callee = re.match(r"[A-Za-z_$][\w$.]*[?!]?\s*(?=\()", code[i:]) if values else None
    end = _balanced_end(code, i + callee.end()) if callee else -1
    return (values, end) if end > 0 else None


def _named_values_at(head: str, code: str, i: int, shape: re.Pattern = _BARE_NAME_ARG_RE) -> tuple[list[int], int] | None:
    """For a bare-name argument at code[i], (the offsets of the values of the name's
    assignments that may reach the use, offset past the name); None when code[i] is not
    a bare name of `shape` or no assignment with a value reaches it. A Python program
    that parses is resolved by scope (_PyReach)."""
    named = shape.match(code, i)
    if not named:
        return None
    reach = _python_reaching(code) if _PYTHON_HEAD_RE.match(head) else None
    if reach is not None:
        values = sorted(v for v in reach.uses.get(i, ()) if v != _OPAQUE)
        return (values, named.end()) if values else None
    values = _span_reaching(head, code, i, named.group("name"))
    return (values, named.end()) if values else None


class _Span(NamedTuple):
    """A function body: [start, end) of its text, its parameters, its parent's index
    (-1 for the program)."""

    start: int
    end: int
    params: frozenset
    parent: int


# Where a function's parameter list opens, per family; the body is the `{…}` after it.
_BRACE_FUNCTION_RES = (
    (re.compile(r"^(?:node|nodejs|bun|deno)"), re.compile(r"\bfunction\b\s*\*?\s*[\w$]*\s*\(")),
    (re.compile(r"^php"), re.compile(r"\bfunction\b\s*&?\s*\w*\s*\(")),
    (re.compile(r"^(?:R$|Rscript)"), re.compile(r"\bfunction\s*\(")),
)
_JS_HEAD_RE = re.compile(r"^(?:node|nodejs|bun|deno)")
_ARROW_BODY_RE = re.compile(r"=>\s*\{")
_PERL_SUB_RE = re.compile(r"\bsub\b\s*[\w:]*\s*\{")
_PERL_PARAMS_RE = re.compile(r"\bmy\s*\(([^)]*)\)\s*=\s*@_")
_PERL_SHIFT_RE = re.compile(r"\s*my\s+(\$\w+)\s*=\s*shift\b")
_PY_DEF_RE = re.compile(r"(?m)^([ \t]*)(?:async[ \t]+)?def[ \t]+\w+[ \t]*\(([^)]*)\)[^\n]*:")
_PY_LAMBDA_RE = re.compile(r"\blambda\b([^:\n]*):")


def _param_names(text: str, head: str) -> frozenset:
    """The names a parameter list binds: `$name` in perl and php, otherwise each
    identifier outside a default value."""
    text = re.sub(r"=[^,]*", "", text)
    if re.match(r"^(?:perl|php)", head):
        return frozenset(re.findall(r"[$@%]\w+", text))
    return frozenset(re.findall(r"[A-Za-z_$][\w$]*", text))


def _matching(view: str, i: int) -> int:
    """Offset of the bracket closing the one at view[i], or -1 when it does not close."""
    pairs, depth = {"(": ")", "{": "}", "[": "]"}, 0
    opener, closer = view[i], pairs[view[i]]
    for j in range(i, len(view)):
        if view[j] == opener:
            depth += 1
        elif view[j] == closer:
            depth -= 1
            if depth == 0:
                return j
    return -1


def _brace_spans(view: str, head: str) -> list:
    """(start, end, params) of each `{…}` function body for a brace-delimited family."""
    found = []
    for head_re, opener_re in _BRACE_FUNCTION_RES:
        if not head_re.match(head):
            continue
        for m in opener_re.finditer(view):
            close = _matching(view, m.end() - 1)
            body = re.match(r"\s*\{", view[close + 1:]) if close >= 0 else None
            if body:
                start = close + 1 + body.end() - 1
                found.append((start, _matching(view, start), _param_names(view[m.end():close], head)))
    if _JS_HEAD_RE.match(head):
        for m in _ARROW_BODY_RE.finditer(view):
            before = view[:m.start()].rstrip()
            if before.endswith(")"):
                depth, j = 0, len(before) - 1
                while j >= 0:
                    depth += {")": 1, "(": -1}.get(before[j], 0)
                    if depth == 0:
                        break
                    j -= 1
                params = _param_names(before[j + 1:-1], head) if j >= 0 else frozenset()
            else:
                name = re.search(r"[A-Za-z_$][\w$]*$", before)
                params = frozenset([name.group()]) if name else frozenset()
            start = m.end() - 1
            found.append((start, _matching(view, start), params))
    if head.startswith("perl"):
        for m in _PERL_SUB_RE.finditer(view):
            start = m.end() - 1
            end = _matching(view, start)
            params = set()
            for pm in _PERL_PARAMS_RE.finditer(view, start + 1, end if end > 0 else len(view)):
                params |= _param_names(pm.group(1), head)
            shifted = _PERL_SHIFT_RE.match(view, start + 1)
            if shifted:
                params.add(shifted.group(1))
            found.append((start, end, frozenset(params)))
    return found


# A JS method or object-literal shorthand: `name(params) {` at a statement start.
_JS_METHOD_RE = re.compile(
    r"(?:(?m:^)|(?<=[;},]))[ \t]*(?:(?:async|static|get|set)[ \t]+|\*[ \t]*)*(?P<name>[A-Za-z_$][\w$]*)[ \t]*\("
)
_JS_NOT_METHOD = frozenset({"if", "for", "while", "switch", "catch", "function", "with", "return"})


def _js_method_spans(view: str) -> list:
    """(start, end, params) of each JS method body."""
    found = []
    for m in _JS_METHOD_RE.finditer(view):
        if m.group("name") in _JS_NOT_METHOD:
            continue
        close = _matching(view, m.end() - 1)
        body = re.match(r"\s*\{", view[close + 1:]) if close >= 0 else None
        if body:
            start = close + 1 + body.end() - 1
            found.append((start, _matching(view, start), _param_names(view[m.end():close], "node")))
    return found


# Block keywords. Lua: function, if and do open, end closes; repeat … until. Ruby: def,
# class, module, begin, case and do open, and if/unless/while/until/for only at a
# statement start; `.end` and `:end` are not keywords.
_LUA_KEYWORD_RE = re.compile(r"(?<![\w.:])(function|if|do|end|repeat|until)(?!\w)")
_RUBY_KEYWORD_RE = re.compile(
    r"(?<![\w.:$@])(def|class|module|begin|case|do|if|unless|while|until|for|end)(?![\w?!:])"
)
_RUBY_STATEMENT_KEYWORDS = frozenset({"if", "unless", "while", "until", "for"})
_RUBY_ENDLESS_DEF_RE = re.compile(r"def\s+[\w.?!]+\s*(?:\([^)]*\))?\s*=(?![=~>])")
_KEYWORD_HEADER_RE = re.compile(r"[ \t]*[\w.:?!]*[ \t]*(?:\(([^)]*)\)|([^\n;]*))")
_RUBY_BLOCK_PARAMS_RE = re.compile(r"[ \t]*\|([^|\n]*)\|")
_RUBY_BRACE_BLOCK_RE = re.compile(r"\{[ \t]*\|([^|\n]*)\|")


def _endless_def_end(view: str, line_start: int) -> int:
    """The end of a ruby endless def whose line starts at view[line_start]: the end of
    that line, extended over each following line indented deeper than it (a body
    written on the next line). Blank lines do not end it."""
    indent = len(view[line_start:]) - len(view[line_start:].lstrip(" \t"))
    end = view.find("\n", line_start)
    while end >= 0:
        nxt = view.find("\n", end + 1)
        line = view[end + 1:len(view) if nxt < 0 else nxt]
        if line.strip() and len(line) - len(line.lstrip(" \t")) <= indent:
            break
        end = nxt
    return len(view) if end < 0 else end


def _keyword_blocks(view: str, head: str) -> tuple | None:
    """For ruby and lua: (function spans, per-offset keyword depth), from counting
    block keywords against `end`; None when they do not balance. A ruby statement
    modifier (`x if y`), the `do` of a `while … do` header on its line and an endless
    def (`def f(x) = …`, a span over its line and the lines indented deeper below it)
    open nothing. A def or a lua
    function is a span with its
    parameters; a ruby `do |a, b|` or `{ |a, b|` block is a span with its block
    parameters."""
    lua = not head.startswith("ruby")
    stack: list = []
    spans: list = []
    deltas: list = []
    header_line = -1
    for m in (_LUA_KEYWORD_RE if lua else _RUBY_KEYWORD_RE).finditer(view):
        word, at = m.group(1), m.start()
        line_start = view.rfind("\n", 0, at) + 1
        if word == "end" or (lua and word == "until"):
            if not stack or (stack[-1][0] == "repeat") != (word == "until"):
                return None
            _, start, params, is_function = stack.pop()
            deltas.append((at, -1))
            if is_function:
                spans.append((start, at, params))
            continue
        if not lua and word in _RUBY_STATEMENT_KEYWORDS:
            prefix = view[max(line_start, view.rfind(";", 0, at) + 1):at].strip()
            if prefix and not prefix.endswith(("=", "(")):
                continue                                 # a statement modifier
            if word in ("while", "until", "for"):
                header_line = line_start
        elif not lua and word == "do" and header_line == line_start:
            header_line = -1
            continue                                     # the header's own `do`
        elif not lua and word == "def" and _RUBY_ENDLESS_DEF_RE.match(view, at):
            header = _KEYWORD_HEADER_RE.match(view, m.end())
            spans.append((m.end(), _endless_def_end(view, line_start), _param_names(header.group(1) or "" if header else "", head)))
            continue                                     # an endless def: no `end`
        start, params, is_function = m.end(), frozenset(), False
        if word in ("function", "def"):
            header = _KEYWORD_HEADER_RE.match(view, m.end())
            text = (header.group(1) or header.group(2) or "") if header else ""
            start, params, is_function = header.end() if header else m.end(), _param_names(text, head), True
        elif word == "do" and not lua:
            bar = _RUBY_BLOCK_PARAMS_RE.match(view, m.end())
            if bar:
                start, params, is_function = bar.end(), _param_names(bar.group(1), head), True
        stack.append((word, start, params, is_function))
        deltas.append((m.end(), 1))
    if stack:
        return None
    if not lua:
        for m in _RUBY_BRACE_BLOCK_RE.finditer(view):
            end = _matching(view, m.start())
            if end < 0:
                return None
            spans.append((m.end(), end, _param_names(m.group(1), head)))
    depth, out, k = 0, [], 0
    deltas.sort()
    for offset in range(len(view) + 1):
        while k < len(deltas) and deltas[k][0] <= offset:
            depth += deltas[k][1]
            k += 1
        out.append(depth)
    return spans, tuple(out)


_KEYWORD_FAMILY_RE = re.compile(r"^(?:ruby|lua|luajit)")


def _python_fallback_spans(view: str) -> list:
    """(start, end, params) of each def body (the lines indented deeper than the def
    line) and each lambda, for Python that does not parse."""
    found = []
    for m in _PY_DEF_RE.finditer(view):
        indent, end = len(m.group(1).expandtabs()), len(view)
        for line in re.finditer(r"(?m)^([ \t]*)\S[^\n]*$", view[m.end():]):
            if len(line.group(1).expandtabs()) <= indent:
                end = m.end() + line.start()
                break
        found.append((m.end(), end, _param_names(m.group(2), "python")))
    for m in _PY_LAMBDA_RE.finditer(view):
        eol = view.find("\n", m.end())
        found.append((m.end(), eol if eol >= 0 else len(view), _param_names(m.group(1), "python")))
    return found


@functools.lru_cache(maxsize=64)
def _scope_spans(code: str, head: str) -> tuple:
    """The function spans of a program without a parser, nested by containment. Empty
    (the whole program is one span) when a body's brackets do not close."""
    view = _index_view(code, head)
    if _PYTHON_HEAD_RE.match(head):
        raw = _python_fallback_spans(view)
    elif _KEYWORD_FAMILY_RE.match(head):
        blocks = _keyword_blocks(view, head)
        raw = list(blocks[0]) if blocks else []
    else:
        raw = _brace_spans(view, head) + (_js_method_spans(view) if _JS_HEAD_RE.match(head) else [])
    if any(end < 0 for _, end, _ in raw):
        return ()
    raw.sort(key=lambda span: (span[0], -span[1]))
    spans: list = []
    for start, end, params in raw:
        parent = -1
        for k in range(len(spans) - 1, -1, -1):
            if spans[k].start <= start and end <= spans[k].end:
                parent = k
                break
        spans.append(_Span(start, end, params, parent))
    return tuple(spans)


def _innermost(spans: tuple, i: int) -> int:
    """Index of the innermost span containing offset i, or -1 for the program."""
    found = -1
    for k, span in enumerate(spans):
        if span.start <= i < span.end:
            found = k
    return found


@functools.lru_cache(maxsize=64)
def _depths(code: str, head: str) -> tuple:
    """Per offset, the nesting depth there: `{` minus `}` before it; for ruby and lua,
    the block keywords open before it (0 everywhere when they do not balance); for
    Python that does not parse, the indentation of its line."""
    view = _index_view(code, head)
    out, depth = [], 0
    if _KEYWORD_FAMILY_RE.match(head):
        blocks = _keyword_blocks(view, head)
        return blocks[1] if blocks else (0,) * (len(view) + 1)
    if _PYTHON_HEAD_RE.match(head):
        for line in view.split("\n"):
            indent = len(line) - len(line.lstrip(" \t"))
            out += [indent] * (len(line) + 1)
        return tuple(out)
    for ch in view:
        out.append(depth)
        depth += {"{": 1, "}": -1}.get(ch, 0)
    out.append(depth)
    return tuple(out)


def _block_of(code: str, head: str, i: int, floor: int) -> tuple:
    """[open, close) of the innermost `{…}` containing offset i and opening at or after
    floor, or (floor, len(code)) when there is none."""
    view = _index_view(code, head)
    depth, j = 0, i - 1
    while j >= floor:
        ch = view[j]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                close = _matching(view, j)
                return j, close if close >= 0 else len(code)
            depth -= 1
        j -= 1
    return floor, len(code)


# A named JS function: `function name(params) {`, `const|let|var name = function(params) {`,
# or `const|let|var name = (params) =>` / `name = param =>`, with a `{…}` body or an
# expression body.
_JS_NAMED_FUNCTION_RE = re.compile(
    r"\bfunction\s*\*?\s*(?P<fname>[A-Za-z_$][\w$]*)\s*\((?P<fparams>[^()]*)\)\s*\{"
    r"|\b(?:const|let|var)\s+(?P<ename>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function\b\s*\*?\s*[\w$]*\s*"
    r"\((?P<eparams>[^()]*)\)\s*\{"
    r"|\b(?:const|let|var)\s+(?P<aname>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?"
    r"(?:\((?P<aparams>[^()]*)\)|(?P<aparam>[A-Za-z_$][\w$]*))\s*=>\s*"
)


class _NamedFunction(NamedTuple):
    """A named function: its parameters in order, its body, and its return values."""

    params: tuple
    start: int
    end: int
    returns: tuple


def _expression_end(view: str, i: int) -> int:
    """Offset where an expression starting at view[i] ends: a `;`, `,` or closing bracket
    at its own depth, or a line break outside brackets."""
    depth = 0
    for j in range(i, len(view)):
        ch = view[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return j
            depth -= 1
        elif depth == 0 and ch in ";,\n":
            return j
    return len(view)


@functools.lru_cache(maxsize=64)
def _js_functions(code: str, head: str) -> dict:
    """Per name, the named functions a JS program declares. A name declared twice is
    left out: which one a call reaches is not known."""
    view = _index_view(code, head)
    found: dict = {}
    for m in _JS_NAMED_FUNCTION_RE.finditer(view):
        block = m.group("fname") or m.group("ename")
        name = block or m.group("aname")
        params = (m.group("fparams") or m.group("eparams") or "") if block else (m.group("aparams") or m.group("aparam") or "")
        names = tuple(
            part.strip() if part.strip().startswith("{") else
            (re.findall(r"^\s*(?:\.\.\.)?([A-Za-z_$][\w$]*)", part) or [""])[0]
            for part in _top_level_parts(params)
        ) if params.strip() else ()
        if block or view[m.end():m.end() + 1] == "{":
            start = m.end() - 1 if block else m.end()
            end = _matching(view, start)
            if end < 0:
                continue
            body = view[start + 1:end]
            nested = [n.start() for n in _JS_NAMED_FUNCTION_RE.finditer(body)] + [n.start() for n in re.finditer(r"\bfunction\b|=>", body)]
            returns = tuple(start + 1 + r.end() for r in re.finditer(r"\breturn\s+", body)
                            if not any(n < r.start() for n in nested))
        else:
            start, end = m.end(), _expression_end(view, m.end())
            returns = (start,)
        found.setdefault(name, []).append(_NamedFunction(names, start, end, returns))
    return {name: fns[0] for name, fns in found.items() if len(fns) == 1}


# Named functions in the other families with function spans, as (header, how its
# parameters are listed). A header ends where the function's span starts.
_HELPER_HEAD_RE = re.compile(r"^(?:ruby|perl|lua|luajit|php|R$|Rscript)")
_HELPER_HEADERS = (
    (re.compile(r"^ruby"), re.compile(r"\bdef[ \t]+(?:self\.)?(?P<name>[A-Za-z_]\w*[?!]?)[ \t]*(?:\((?P<params>[^()]*)\)|(?P<bare>[^\n;=()]*))")),
    (re.compile(r"^perl"), re.compile(r"\bsub\s+(?P<name>[A-Za-z_]\w*)\s*")),
    (re.compile(r"^(?:lua|luajit)"), re.compile(r"\bfunction\s+(?P<name>[A-Za-z_][\w.]*)\s*\((?P<params>[^()]*)\)")),
    (re.compile(r"^php"), re.compile(r"\bfunction\s+&?\s*(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^()]*)\)\s*")),
    (re.compile(r"^(?:R$|Rscript)"), re.compile(r"(?<![\w.])(?P<name>[A-Za-z_.][\w.]*)\s*(?:<-|=)\s*function\s*\((?P<params>[^()]*)\)\s*")),
)
# Perl names its parameters inside the body: `my ($a, $b) = @_;` or `my $a = shift;`.
_PERL_ARGS_RE = re.compile(r"\bmy\s*\(([^)]*)\)\s*=\s*@_|\bmy\s+(\$\w+)\s*=\s*shift\b")


def _ordered_params(text: str, head: str) -> tuple:
    """The parameter names a parameter list binds, in order (`$name` in perl and php)."""
    pattern = r"[$@%]\w+" if re.match(r"^(?:perl|php)", head) else r"[A-Za-z_.][\w.]*"
    return tuple((re.findall(pattern, part) or [""])[0] for part in re.sub(r"=[^,]*", "", text).split(",")) if text.strip() else ()


def _last_statement(view: str, start: int, end: int) -> int:
    """Offset of the last statement in view[start:end] at the body's own depth, or -1."""
    depth, last = 0, -1
    k = start
    while k < end:
        ch = view[k]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch in ";\n":
            nxt = _skip(r"[ \t;\n]*", view, k)
            if nxt < end:
                last = nxt
            k = nxt
            continue
        k += 1
    first = _skip(r"[ \t;\n]*", view, start)
    return last if last >= 0 else (first if first < end else -1)


@functools.lru_cache(maxsize=64)
def _other_functions(code: str, head: str) -> dict:
    """Per name, the named functions a ruby, perl, lua, php or R program declares, each
    with its parameters in order and its return values: every `return` value in its
    own body, plus the last expression of a ruby or R body. A name declared twice is
    left out."""
    view = _index_view(code, head)
    header = next((h for family, h in _HELPER_HEADERS if family.match(head)), None)
    if header is None:
        return {}
    spans = _scope_spans(code, head)
    found: dict = {}
    for m in header.finditer(view):
        params = _ordered_params(m.groupdict().get("params") or m.groupdict().get("bare") or "", head)
        span = next((sp for sp in spans if m.end() - 1 <= sp.start <= _skip(r"\s*", view, m.end())), None)
        if span is None and _R_HEAD_RE.match(head) and view[m.end():m.end() + 1] != "{":
            start, end = m.end(), _expression_end(view, m.end())
        elif span is None:
            continue
        else:
            start, end = span.start, span.end
        body = view[start:end]
        if re.match(r"^perl", head):
            given = _PERL_ARGS_RE.findall(body)
            params = tuple(n for listed, one in given for n in (re.findall(r"[$@%]\w+", listed) if listed else [one]))
        nested = [sp.start - start for sp in spans if start < sp.start < end]
        returns = [start + r.end() for r in re.finditer(r"\breturn\b[ \t(]*", body) if not any(n < r.start() for n in nested)]
        if re.match(r"^ruby|^R$|^Rscript", head):
            body_start = start + 1 if view[start:start + 1] == "{" else start
            last = _last_statement(view, body_start, end - (1 if view[end - 1:end] == "}" else 0))
            if last >= 0 and not re.match(r"(?:return|if|unless|while|until|for|case|begin|end|puts|print)\b", view[last:]):
                returns.append(last)
        found.setdefault(m.group("name"), []).append(_NamedFunction(params, start, end, tuple(sorted(set(returns)))))
    return {name: fns[0] for name, fns in found.items() if len(fns) == 1}


def _top_level_parts(text: str) -> list:
    """`text` split at the commas outside brackets."""
    parts, depth, last = [], 0, 0
    for k, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[last:k])
            last = k + 1
    return parts + [text[last:]]


def _destructured_key(pattern: str, name: str) -> str | None:
    """For a `{…}` destructuring pattern, the key that binds `name` (`{cmd}` -> cmd,
    `{c: cmd}` -> c, `{cmd = "x"}` -> cmd), or None."""
    if not pattern.startswith("{"):
        return None
    for part in _top_level_parts(pattern[1:-1]):
        m = re.match(r"\s*([A-Za-z_$][\w$]*)\s*(?::\s*([A-Za-z_$][\w$]*))?\s*(?:=.*)?$", part, re.S)
        if m and (m.group(2) or m.group(1)) == name:
            return m.group(1)
    return None


# A key of an object or dict literal at its top level: `cmd:`, `"cmd":`, `'cmd':`.
def _object_key_values(head: str, code: str, i: int, key: str, _depth: int = 0) -> list[int]:
    """For the object or dict literal starting at code[i] (after whitespace), the offsets
    of the values its constant `key` may hold: a later entry wins, as at run time, so a
    later `key:` replaces an earlier one and a spread (`...a`, `**a`) of a literal that
    sets the key replaces it too; a spread that may or may not set it keeps the earlier
    value as one of the possibilities. [] when there is no literal or no such key."""
    i = _skip(r"\s*", code, i)
    view = _mask_string_literals(code)
    if view[i:i + 1] != "{":
        return []
    close = _matching(view, i)
    if close < 0:
        return []
    starts, depth = [i + 1], 0
    for k in range(i + 1, close):
        if view[k] in "([{":
            depth += 1
        elif view[k] in ")]}":
            depth -= 1
        elif view[k] == "," and depth == 0:
            starts.append(k + 1)
    key_re = re.compile(r"\s*(?:([\"'])" + re.escape(key) + r"\1|" + re.escape(key) + r"(?![\w$]))\s*:\s*")
    found: list[int] = []
    for start in starts:
        spread = re.match(r"\s*(?:\.\.\.|\*\*)\s*", view[start:close])
        if spread:
            at = start + spread.end()
            named = _named_values_at(head, code, at, _NAME_OPERAND_RE) if _depth < _MAX_NAME_DEPTH else None
            holders = named[0] if named else ([at] if view[at:at + 1] == "{" else [])
            supplied = [_object_key_values(head, code, h, key, _depth + 1) for h in holders]
            given = [v for values in supplied for v in values]
            found = given if holders and all(supplied) else found + given
            continue
        m = key_re.match(code, start)
        if m:
            found = [m.end()]
    return found


# A constant-key read of a name: `opts["cmd"]`, `opts['cmd']`, `opts.get("cmd")` and,
# outside Python, `opts.cmd`.
_MEMBER_READ_RE = re.compile(
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?:\[\s*(?P<q>[\"'])(?P<k1>[\w$-]+)(?P=q)\s*\]"
    r"|\.\s*get\s*\(\s*(?P<q2>[\"'])(?P<k2>[\w$-]+)(?P=q2)\s*\)"
    r"|\.(?P<k3>[A-Za-z_$][\w$]*)(?!\s*\())"
)


def _member_values_at(head: str, code: str, i: int) -> tuple[list[int], int] | None:
    """For a constant-key read at code[i] of a name whose values are object or dict
    literals, (the offsets of the values that key holds in them, offset past the read);
    None otherwise. `.key` is a member read only outside Python."""
    m = _MEMBER_READ_RE.match(code, i)
    if not m or (m.group("k3") and _PYTHON_HEAD_RE.match(head)):
        return None
    key = m.group("k1") or m.group("k2") or m.group("k3")
    reach = _python_reaching(code) if _PYTHON_HEAD_RE.match(head) else None
    if reach is not None:
        holders = [v for v in reach.uses.get(i, ()) if v != _OPAQUE]
    else:
        holders = _span_reaching(head, code, i, m.group("name"))
    for _ in range(_MAX_NAME_DEPTH):                  # a holder that is itself a name
        named = [_named_values_at(head, code, h) for h in holders]
        if not any(named):
            break
        holders = [v for h, n in zip(holders, named) for v in (n[0] if n else [h])]
    values = sorted({v for h in holders for v in _object_key_values(head, code, h, key)})
    return (values, m.end()) if values else None


def _named_functions(code: str, head: str) -> dict:
    """The named functions a program declares, for the families whose helpers are read."""
    if _JS_HEAD_RE.match(head):
        return _js_functions(code, head)
    return _other_functions(code, head) if _HELPER_HEAD_RE.match(head) else {}


# A call that is not the function's own declaration.
_DECLARATION_BEFORE_RE = re.compile(r"\b(?:function\s*\*?|def|sub)\s*&?\s*$")


def _calls(code: str, head: str, name: str) -> list:
    """The argument offsets of each call `name(…)` (and, in ruby and perl, `name "…"`
    without parentheses) outside its own declaration; [] for a call with a spread or
    splat argument. A JS `[…].forEach(name)` / `.map(name)` calls it once per element."""
    view = _index_view(code, head)
    out = [[e] for callee, elements in (_js_element_bindings(code, head)[1] if _JS_HEAD_RE.match(head) else ())
           if callee == name for e in elements]
    for m in re.finditer(r"(?<![\w$.:>@])" + re.escape(name) + r"(?:\s*\(|[ \t]+(?=[\"'$\[]))", view):
        if _DECLARATION_BEFORE_RE.search(view[:m.start()]):
            continue
        if view[m.end() - 1] == "(":
            close = _matching(view, m.end() - 1)
        elif re.match(r"^(?:ruby|perl)", head):
            close = _skip(r"[^;\n]*", view, m.end())
        else:
            continue
        if close < 0:
            continue
        args, k = [], m.end()
        while k < close:
            k = _skip(r"\s*", view, k)
            if k >= close:
                break
            args.append(k)
            k = min(_expression_end(view, k), close)
            k = k + 1 if view[k:k + 1] == "," else k
        out.append([] if any(view[a:a + 3] == "..." or view[a:a + 1] == "*" or view[a:a + 2] == "@_" for a in args) else args)
    return out


def _literal_list_elements(view: str, i: int) -> list | None:
    """The offsets of the elements of the `[…]` literal at view[i] (strings masked), or
    None when it does not close (a `...` element reads nothing)."""
    close = _matching(view, i) if view[i:i + 1] == "[" else -1
    if close < 0:
        return None
    out, k = [], i + 1
    while k < close:
        k = _skip(r"\s*", view, k)
        if k >= close:
            break
        out.append(k)
        k = min(_expression_end(view, k), close)
        k = k + 1 if view[k:k + 1] == "," else k
    return out


# A literal JS list handed element by element to a callback or a loop:
# `[…].forEach(f)` / `.map(f)`, `[…].forEach((c) => …)` / `c => …` / `function (c) {…}`,
# and `for (const c of […])`.
_JS_EACH_RE = re.compile(r"\]\s*\.\s*(?:forEach|map)\s*\(\s*")
_JS_FOR_OF_RE = re.compile(r"\bfor\s*\(\s*(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s+of\s+(?=\[)")
_JS_INLINE_CALLBACK_RE = re.compile(
    r"(?:\(\s*(?P<p1>[A-Za-z_$][\w$]*)\s*\)|(?P<p2>[A-Za-z_$][\w$]*))\s*=>\s*"
    r"|function\s*\(\s*(?P<p3>[A-Za-z_$][\w$]*)\s*\)\s*(?=\{)"
)


def _list_opening(view: str, close: int) -> int:
    """The offset of the `[` that the `]` at view[close] closes, or -1."""
    depth = 0
    for k in range(close, -1, -1):
        if view[k] in ")]}":
            depth += 1
        elif view[k] in "([{":
            depth -= 1
            if depth == 0:
                return k if view[k] == "[" else -1
    return -1


@functools.lru_cache(maxsize=64)
def _js_element_bindings(code: str, head: str) -> tuple:
    """(name, body start, body end, element offsets) for each literal JS list whose
    elements a loop or an inline callback binds to a name, and (callee name, element
    offsets) pairs for a named callback (`[…].forEach(f)`)."""
    view = _index_view(code, head)
    bound, named = [], []
    for m in _JS_EACH_RE.finditer(view):
        opening = _list_opening(view, m.start())
        elements = _literal_list_elements(view, opening) if opening >= 0 else None
        if elements is None:
            continue
        callee = re.match(r"([A-Za-z_$][\w$]*)\s*\)", view[m.end():])
        inline = _JS_INLINE_CALLBACK_RE.match(view, m.end())
        if inline:
            name = inline.group("p1") or inline.group("p2") or inline.group("p3")
            start = inline.end()
            bound.append((name, start, _expression_end(view, start), tuple(elements)))
        elif callee:
            named.append((callee.group(1), tuple(elements)))
    for m in _JS_FOR_OF_RE.finditer(view):
        elements = _literal_list_elements(view, m.end())
        head_close = _matching(view, m.start() + view[m.start():].index("("))
        if elements is None or head_close < 0:
            continue
        start = _skip(r"\s*", view, head_close + 1)
        end = _matching(view, start) if view[start:start + 1] == "{" else _expression_end(view, start)
        bound.append((m.group("name"), start, end, tuple(elements)))
    return tuple(bound), tuple(named)


def _param_values(head: str, code: str, i: int, name: str) -> list | None:
    """For a use at code[i] of a parameter of the innermost named function holding it,
    the offsets of the arguments its calls pass in that position; None when the use is
    not such a parameter, or the body assigns it before the use (perl's own
    `my $x = shift` / `= @_` binding excepted). A name a JS `{…}` pattern destructures
    reads the value its key holds in each literal object passed there."""
    fns = [f for f in _named_functions(code, head).items() if f[1].start <= i < f[1].end
           and (name in f[1].params or any(_destructured_key(p, name) for p in f[1].params))]
    if not fns:
        return None
    fname, fn = max(fns, key=lambda f: f[1].start)
    starts, values = _assignments(code, head).get(name, ([], []))
    view = _index_view(code, head)
    if any(fn.start <= at < i and not re.match(r"shift\b|@_", view[v:]) for at, v in zip(starts, values)):
        return None                                      # reassigned in the body first
    if name in fn.params:
        position = fn.params.index(name)
        return sorted({args[position] for args in _calls(code, head, fname) if position < len(args)})
    position, key = next((k, key) for k, p in enumerate(fn.params) if (key := _destructured_key(p, name)))
    calls = [args for args in _calls(code, head, fname) if position < len(args)]
    return sorted({v for args in calls for v in _object_key_values(head, code, args[position], key)})


def _span_reaching(head: str, code: str, i: int, name: str) -> list:
    """The literal values of `name` reaching a use at code[i], for a program without a
    parser: the assignments in the use's own function span (outside nested spans),
    walked back from the use until one at the use's depth or shallower, which dominates
    it; a parameter is unreadable; a name free in its span takes every assignment of it
    in the enclosing span, outward until one has any; an assignment only after the use
    leaves it unresolved. A JS `let`/`const` inside a block reaches only uses in that
    block. Ruby and lua spans come from their block keywords. A parameter of a named
    function (JS, ruby, perl, lua, php, R) reads the arguments its calls pass for it, and
    a JS name a loop or inline callback binds to a literal list reads its elements."""
    if _JS_HEAD_RE.match(head):
        bound = [b for b in _js_element_bindings(code, head)[0] if b[0] == name and b[1] <= i < b[2]]
        inner = max(bound, key=lambda b: b[1]) if bound else None
        if inner and not any(inner[1] <= at < i for at in _assignments(code, head).get(name, ([], []))[0]):
            return sorted(inner[3])
    if _JS_HEAD_RE.match(head) or _HELPER_HEAD_RE.match(head):
        passed = _param_values(head, code, i, name)
        if passed is not None:
            return passed
    spans, depth = _scope_spans(code, head), _depths(code, head)
    starts, values = _assignments(code, head).get(name, ([], []))
    enclosing = _enclosing_assignments(code, head)

    def home(k: int) -> int:
        """The span an assignment binds in: an R `<<-` binds in its span's parent."""
        inner = _innermost(spans, starts[k])
        return spans[inner].parent if starts[k] in enclosing and inner >= 0 else inner

    span = _innermost(spans, i)
    floor = spans[span].start if span >= 0 else 0
    own = [k for k in range(len(starts)) if home(k) == span]
    params = spans[span].params if span >= 0 else frozenset()
    if not own and name not in params:
        while span >= 0:
            span = spans[span].parent
            outer = [values[k] for k in range(len(starts)) if home(k) == span]
            if outer:
                return sorted(outer)
        return []
    view = _index_view(code, head) if _JS_HEAD_RE.match(head) else ""
    found = []
    for k in sorted((k for k in own if starts[k] < i), key=lambda k: -starts[k]):
        if view and re.search(r"\b(?:let|const)\s+$", view[floor:starts[k]]):
            block = _block_of(code, head, starts[k], floor)
            if not block[0] <= i < block[1]:
                continue
        found.append(values[k])
        if depth[starts[k]] <= depth[i]:
            break
    return sorted(found)


def _argument_readings(head: str, code: str, at: int) -> tuple[list[tuple[bool, list[str]]], int] | None:
    """The readings of one call argument at code[at]: (is a string expression, the argv
    items it adds), and the offset after it; None when it is neither a list nor a string
    expression."""
    lst = _list_expr_at(code, at, head)
    if lst:
        return [(False, items) for items in lst[0]], lst[1]
    expr = _string_expr_at(code, at, head)
    if expr:
        return [(True, [text]) for text in expr[0]], expr[1]
    return None


def _argument_commands(code: str, pos: int, skip_leading: int = 0, head: str = "") -> list[str]:
    """The command a call's leading arguments spell. One string expression alone is a
    shell command line (literal, f-string, concatenation, `.format`, `shlex.split`).
    Otherwise consecutive string expressions and list literals are argv, joined
    (`system('gh', 'pr', 'merge', '5')`, `spawnSync('gh', ['pr', 'merge'])`,
    `run(['gh', 'pr', 'merge', '5'])`, `run(args=[…])`). An object literal ends the walk,
    contributing its own `args:` or `cmd:` list (`new Deno.Command('gh', {args: […]})`,
    `Deno.run({cmd: […]})`). Stops at the first other argument. Up to `skip_leading`
    non-literal arguments before the first literal are skipped (a spawn mode, a perl
    open3 handle). An argument that is a bare name reads as each literal value its
    reaching assignments gave it (`cmd = ["gh", …]; run(cmd)`). Every reading is
    returned twice over: a leading string alone as a shell line (`popen("…", "r")`),
    and the whole argv."""
    i = _skip(r"\s*", code, pos)
    keyword = re.match(r"(?:args|cmd)\s*=(?!=)\s*", code[i:])
    if keyword:
        i += keyword.end()
    for _ in range(skip_leading):
        if code[i:i + 1] == "{" or _literal_list_at(code, i, head) or _PREFIXED_LITERAL_RE.match(code, i):
            break
        perl_var = re.match(r"(?:my\s+)?[$@%]\w+", code[i:])
        end = i + perl_var.end() if perl_var else _operand_end(code, i)
        sep = re.match(r"\s*,\s*", code[end:]) if end > 0 else None
        if not sep:
            return []
        i = end + sep.end()
    # Each state is one reading so far: (argv, the leading string when it led).
    states: list[tuple[list[str], str | None]] = [([], None)]
    while True:
        named = _named_values_at(head, code, i)
        readings: list[tuple[bool, list[str]]] = []
        end = -1
        for at in named[0] if named else [i]:
            got = _argument_readings(head, code, at)
            if got:
                readings += got[0]
                end = named[1] if named else got[1]
        if not readings:
            options = _object_argv_at(code, i, head)
            if options:
                states = [(argv + items, first) for argv, first in states for items in options]
            break
        states = [
            (argv + items, first if argv else (items[0] if is_string else None))
            for argv, first in states for is_string, items in readings
        ][:_MAX_READINGS]
        sep = re.match(r"\s*,\s*", code[end:])
        if not sep:
            break
        i = end + sep.end()
    cmds: list[str] = []
    for argv, first in states[:_MAX_READINGS]:
        for cmd in ([first] if first is not None else []) + ([shlex.join(argv)] if len(argv) > 1 else []):
            if cmd not in cmds:
                cmds.append(cmd)
    return cmds


# An interpreter's head word anywhere in a (quote-masked) command.
_INTERPRETER_WORD_RE = re.compile(
    r"(?<![\w./-])(?:[^\s|;&<>()]*/)?(?:" + _INTERPRETER_HEAD_ALTERNATION + r")(?=[ \t]|$)"
)

# Per interpreter family: the flags whose NEXT token is the program, the flags that take a
# value (skipped with it), and whether a combined short flag ending in e/E/c carries code.
_PROGRAM_FLAGS = (
    (re.compile(r"^(?:python|pypy)"), {"-c"}, {"-W", "-X", "--check-hash-based-pycs"}, "c"),
    (re.compile(r"^perl"), {"-e", "-E"}, {"-I", "-M", "-m", "-x"}, "eE"),
    (re.compile(r"^ruby"), {"-e"}, {"-r", "-I", "-C", "-E", "-x"}, "e"),
    (re.compile(r"^(?:node|nodejs|bun)"), {"-e", "--eval", "-p", "--print"},
     {"-r", "--require", "--import", "--loader", "--conditions", "-C"}, ""),
    (re.compile(r"^php"), {"-r"}, {"-c", "-d", "-z"}, ""),
    (re.compile(r"^(?:lua|luajit)"), {"-e"}, {"-l"}, ""),
    (re.compile(r"^(?:R|Rscript)$"), {"-e"}, set(), ""),
    (re.compile(r"^osascript$"), {"-e"}, {"-l"}, ""),
)
_AWK_HEAD_RE = re.compile(r"^(?:awk|gawk|mawk|nawk)$")


# deno flags that take a separate value (`deno eval --ext ts '…'`).
_DENO_VALUE_FLAGS = frozenset({
    "--ext", "--config", "-c", "--import-map", "-L", "--log-level", "--location", "--seed",
    "--cert", "--env-file", "--lock",
})


def _program_from_tokens(head: str, tokens: list) -> str | None:
    """The program text an interpreter is given on its command line, from its shell
    tokens (tokens[0] is the interpreter), or None when it runs a script, a module or
    stdin. Several -e programs are joined with newlines."""
    if head == "deno":
        rest, i = [], 1
        while i < len(tokens):
            t = tokens[i]
            if t in _DENO_VALUE_FLAGS:
                i += 2
                continue
            if not t.startswith("-"):
                rest.append(t)
            i += 1
        return rest[1] if len(rest) >= 2 and rest[0] == "eval" else None
    if _AWK_HEAD_RE.match(head):
        i = 1
        while i < len(tokens):
            t = tokens[i]
            if t == "--":
                i += 1
                break
            if t in ("-f", "--file", "-E", "--exec") or t.startswith("-f"):
                return None                              # the program is in a file
            if t in ("-F", "-v", "-i", "-l", "--field-separator", "--assign", "--include", "--load"):
                i += 2
                continue
            if not t.startswith("-") or t == "-":
                break
            i += 1
        return tokens[i] if i < len(tokens) else None
    for head_re, code_flags, value_flags, combined in _PROGRAM_FLAGS:
        if not head_re.match(head):
            continue
        programs, i = [], 1
        while i < len(tokens):
            t = tokens[i]
            if t in code_flags or (
                combined and re.fullmatch(r"-[A-Za-z]+", t) and t[-1] in combined and len(t) > 2
            ):
                if i + 1 < len(tokens):
                    programs.append(tokens[i + 1])
                i += 2
                continue
            if t in value_flags:
                i += 2
                continue
            if t.startswith("--") and "=" in t:
                i += 1
                continue
            if t == "-" or not t.startswith("-") or t in ("-m",):
                break
            i += 1
        return "\n".join(programs) if programs else None
    return None


# Call names whose last segment runs a process (any qualifier: os.system, Bun.spawn,
# child_process's execSync, IO.popen, …). `execute` is Lua's os.execute only.
_EXEC_CALL_NAMES = frozenset({
    "system", "system2", "popen", "popen2", "popen3", "popen4", "exec", "execl", "execle",
    "execlp", "execlpe", "execv", "execve", "execvp", "execvpe", "execsync", "execfile",
    "execfilesync", "spawn", "spawnsync", "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe", "posix_spawn", "posix_spawnp", "fork",
    "forkpty", "shell_exec", "passthru", "proc_open", "pcntl_exec", "readpipe",
    "getoutput", "getstatusoutput", "check_output", "check_call", "binding",
    "create_subprocess_shell", "create_subprocess_exec",
})
# Modules every attribute of which is a process route (subprocess.run, Open3.capture2, …).
_EXEC_MODULES = frozenset({"subprocess", "child_process", "pty", "commands", "open3", "open2", "ipc"})
# Bare reflective calls that can reach any of the above by name.
_REFLECTIVE_CALLS = frozenset({
    "eval", "exec", "compile", "getattr", "vars", "globals", "locals", "__import__",
    "methodcaller", "call_user_func", "call_user_func_array", "instance_eval", "class_eval",
})
_CALL_NAME_RE = re.compile(r"(?<![\w$])((?:[A-Za-z_$][\w$]*(?:\.|::))*[A-Za-z_$][\w$]*)\s*\(")
_PY_FROM_IMPORT_RE = re.compile(
    r"\bfrom\s+(subprocess|os|pty|commands)\s+import\s*(?:\(([^)]*)\)|([\w \t,*]+))"
)
_PY_IMPORT_AS_RE = re.compile(r"\bimport[ \t]+([\w \t,]+)")
_JS_TAGGED_TEMPLATE_RE = re.compile(r"\$\s*`")
_AWK_PIPE_RE = re.compile(r"\b(?:print|printf)\b[^;}\n]*(?<!\|)\|(?!\|)|\|\s*getline\b|\|&")
_STATEMENT_FORMS = (
    # ruby / perl run a command without parentheses: system "…", exec "…", spawn "…"
    (re.compile(r"^(?:ruby|perl)"), re.compile(r"(?<![\w$.:])(?:system|exec|spawn)[ \t]+(?=[^\s=;,)\]}])")),
    (re.compile(r"^perl"), re.compile(r"(?<![\w$])(?:qx\s*[^\w\s]|readpipe\b|open\s+[\w$]+\s*,|(?:use|require)\s+IPC::)")),
    (re.compile(r"^ruby"), re.compile(r"%x\s*[^\w\s]|(?:__send__|public_send|send)\s*\(?\s*:(?:system|exec|spawn|`|popen)")),
    (re.compile(r"^osascript$"), re.compile(r"\bdo\s+shell\s+script\b")),
    (re.compile(r"^(?:python|pypy)"), re.compile(r"(?<![\w$])(?:__dict__|__builtins__|builtins)\b")),
)


# The process-starting names a `from <module> import *` brings in as bare calls.
_STAR_IMPORT_CALLS = {
    "subprocess": ("run", "call", "check_call", "check_output", "popen", "getoutput",
                   "getstatusoutput"),
    "commands": ("getoutput", "getstatusoutput"),
    "pty": ("spawn", "fork"),
    "os": (),  # every os process call is already a primitive by its own name
}


def _python_aliases(masked: str) -> tuple[set, set]:
    """Names a Python program imported as process routes: bare names (`from subprocess
    import run`, `from os import system`) and aliases of modules EVERY attribute of which
    is a process route (`import subprocess as sp`). An os alias (`import os as o`) is not
    one: `o.system(…)` already counts through its attribute name, `o.path.join` must not."""
    bare, modules = set(), set()
    for m in _PY_FROM_IMPORT_RE.finditer(masked):
        for part in (m.group(2) or m.group(3) or "").split(","):
            words = part.split()
            if not words:
                continue
            if words == ["*"]:
                bare.update(_STAR_IMPORT_CALLS.get(m.group(1), ()))
                continue
            name, alias = words[0], words[-1]
            if m.group(1) != "os" or name.lower() in _EXEC_CALL_NAMES:
                bare.add(alias.lower())
    for m in _PY_IMPORT_AS_RE.finditer(masked):
        for part in m.group(1).split(","):
            words = part.split()
            if len(words) == 3 and words[1] == "as" and words[0] in ("subprocess", "pty"):
                modules.add(words[2].lower())
    return bare, modules


def _perl_aliases(masked: str) -> tuple[set, set]:
    """Bare names a perl program imported as process routes (`use IPC::Run`)."""
    bare = set()
    if re.search(r"\buse\s+IPC::Run\b", masked):
        bare |= {"run", "start"}
    if re.search(r"\buse\s+IPC::Run3\b", masked):
        bare.add("run3")
    return bare, set()


def _language_aliases(head: str, masked: str) -> tuple[set, set]:
    if re.match(r"^(?:python|pypy)", head):
        return _python_aliases(masked)
    if head.startswith("perl"):
        return _perl_aliases(masked)
    return set(), set()


def _is_primitive_call(name: str, head: str, aliases: tuple) -> bool:
    segs = [x.lower() for x in re.split(r"\.|::", name)]
    first, last = segs[0], segs[-1]
    bare, modules = aliases
    if len(segs) == 1 and (last in bare or last in _REFLECTIVE_CALLS):
        return True
    if first in _EXEC_MODULES or first in modules:
        return True
    if last in _EXEC_CALL_NAMES or last == "import_module":
        return True
    if last == "execute" and re.match(r"^(?:lua|luajit)", head):
        return True
    if first == "deno" and last in ("run", "command"):
        return True
    if last == "open" and re.match(r"^(?:perl|ruby)", head):
        return True
    return last == "pipe" and head in ("R", "Rscript")


def _program_names_primitive(head: str, program: str) -> bool:
    """True iff `head`'s program reaches a process: a call or statement shaped like one
    of the language's exec primitives, judged OUTSIDE string literals (a primitive name
    inside a string or a longer word is data), or a backtick / pipe in a language where
    those run a command (ruby/perl/php backticks, a `$`-tagged JS template, awk's
    print | and | getline)."""
    masked = _mask_string_literals(program, head)
    aliases = _language_aliases(head, masked)
    for m in _CALL_NAME_RE.finditer(masked):
        if _is_primitive_call(m.group(1), head, aliases):
            return True
    for head_re, form in _STATEMENT_FORMS:
        if head_re.match(head) and form.search(masked):
            return True
    if _BACKTICK_EXEC_HEAD_RE.match(head) and "`" in masked:
        return True
    if re.match(r"^(?:node|nodejs|bun|deno)$", head) and _JS_TAGGED_TEMPLATE_RE.search(masked):
        return True
    if _AWK_HEAD_RE.match(head) and _AWK_PIPE_RE.search(masked):
        return True
    return bool(re.match(r"^(?:tclsh|wish)", head) and _TCL_EXEC_RE.search(masked))


def _guarded_commands_directly(head: str, code: str, _depth: int = 0, found: list[str] | None = None) -> list[str]:
    """Every guarded command `head`'s program `code` passes DIRECTLY to an exec
    primitive, in program order: the first argument of a primitive-named call that is a
    string or list literal the danger scan reads as a guarded command
    (`os.system("gh pr merge 5")`, `subprocess.run(["gh", "pr", "merge", "5"])`,
    `execSync('…')`), a Tcl `exec` line, a backtick span that runs a shell, a statement
    form, and the same inside an eval/exec of a literal. Call names, backticks and Tcl
    lines written inside a string literal are data. Every one is collected, not only
    the first, so a reader can tell one guarded command from several. Each is appended
    to `found` as it is read, so a caller's list keeps the ones read before a parser
    defect raises."""
    found = [] if found is None else found
    if _depth >= _DIRECT_CALL_MAX_DEPTH or not _program_names_primitive(head, code):
        return found
    masked = _mask_string_literals(code, head)
    aliases = _language_aliases(head, masked)
    for m in re.finditer(r"(\$?)`([^`]*)`", masked):
        if m.group(1) or _BACKTICK_EXEC_HEAD_RE.match(head):
            span = code[m.start(2):m.end(2)]
            if span and _is_dangerous_at_depth(span, _depth + 1):
                found.append(span)
    if re.match(r"^(?:tclsh|wish)[0-9.]*$", head):
        for m in _TCL_EXEC_RE.finditer(masked):
            line = code[m.start(1):m.end(1)].strip()
            if _is_dangerous_at_depth(line, _depth + 1):
                found.append(line)
    for m in _PRIMITIVE_CALL_RE.finditer(masked):
        if not _is_primitive_call(m.group(1), head, aliases):
            continue
        name = m.group(1).lower()
        if _runs_own_code(name, head):
            # eval (and Python's exec) of a literal runs it as the SAME language's code
            expr = _string_expr_at(code, m.end(), head)
            for text in expr[0] if expr else []:
                _guarded_commands_directly(head, text, _depth + 1, found)
            continue
        last = re.split(r"\.|::", name)[-1]
        cmds = _argument_commands(
            code, m.end(),
            skip_leading=3 if last in ("open2", "open3", "run3") or last.startswith("spawn") else 0,
            head=head,
        )
        found += [c for c in cmds if _is_dangerous_at_depth(c, _depth + 1)]
    found += [cmd for cmd in _statement_commands(head, code, masked) if _is_dangerous_at_depth(cmd, _depth + 1)]
    return found


def _code_runs_guarded_directly(head: str, code: str, _depth: int = 0) -> bool:
    """True iff `head`'s program `code` passes a guarded command directly to an exec
    primitive (see _guarded_commands_directly)."""
    return bool(_guarded_commands_directly(head, code, _depth))


def _runs_own_code(name: str, head: str) -> bool:
    """A bare call that runs its string argument as the interpreter's OWN code: eval in
    every language here, exec in Python only (elsewhere exec runs a shell command)."""
    if name == "eval":
        return bool(re.match(r"^(?:python|pypy|node|nodejs|bun|deno|ruby|perl|php|lua|luajit)", head))
    return name == "exec" and bool(re.match(r"^(?:python|pypy)", head))


# Statement-shaped routes whose command is not a call's first argument.
_QX_RE = re.compile(r"(?<![\w$])(?:qx|%x)\s*(?P<open>[(\[{<|!/])")
_CLOSERS = {"(": ")", "[": "]", "{": "}", "<": ">"}
_OSASCRIPT_SHELL_RE = re.compile(r"\bdo\s+shell\s+script\s+")
_AWK_PRINT_PIPE_RE = re.compile(r"\b(?:print|printf)\b[^;}\n|]*?(?<!\|)\|&?\s*")
_AWK_GETLINE_RE = re.compile(r"\|&?\s*getline\b")
_PERL_OPEN_RE = re.compile(r"(?<![\w$])open\s*\(?\s*(?:my\s+)?[\w$]+\s*,\s*")
_RUBY_SEND_RE = re.compile(r"(?:__send__|public_send|send)\s*\(?\s*:(?:system|exec|spawn|`)\s*,\s*")


def _perl_open_commands(code: str, i: int, head: str = "") -> list[str]:
    """The commands a perl `open` runs: the 2-argument form's `"| cmd"` / `"cmd |"`, or
    the 3-argument form's command after a `-|` / `|-` mode, for each reading."""
    readings: list[list[str]] = [[]]
    while True:
        expr = _string_expr_at(code, i, head)
        if not expr:
            break
        readings = _with_each(readings, expr[0])
        sep = re.match(r"\s*,\s*", code[expr[1]:])
        if not sep:
            break
        i = expr[1] + sep.end()
    out = []
    for texts in readings:
        if len(texts) >= 2 and texts[0].strip() in ("-|", "|-"):
            out.append(shlex.join(texts[1:]) if len(texts) > 2 else texts[1])
        elif texts and re.search(r"^\s*\||\|\s*$", texts[0]):
            out.append(texts[0].strip().strip("|").strip())
    return out


def _statement_commands(head: str, code: str, masked: str) -> list[str]:
    """Commands run through a statement form rather than a call's first argument: perl
    `qx(…)` and ruby `%x(…)`, osascript `do shell script "…"`, awk `print … | "cmd"` and
    `"cmd" | getline`, perl `open(F, "| cmd")` / `open(F, "-|", …)`, ruby
    `send(:system, …)`."""
    out = []
    if re.match(r"^(?:perl|ruby)", head):
        for m in _QX_RE.finditer(masked):
            close = _CLOSERS.get(m.group("open"), m.group("open"))
            end = code.find(close, m.end())
            if end > 0:
                out.append(code[m.end():end])
    if head == "osascript":
        for m in _OSASCRIPT_SHELL_RE.finditer(masked):
            expr = _string_expr_at(code, m.end(), head)
            if expr:
                out += expr[0]
    if _AWK_HEAD_RE.match(head):
        for m in _AWK_PRINT_PIPE_RE.finditer(masked):
            expr = _string_expr_at(code, m.end(), head)
            if expr:
                out += expr[0]
        for m in _AWK_GETLINE_RE.finditer(masked):
            before = [lit for lit in _PREFIXED_LITERAL_RE.finditer(code, 0, m.start())]
            if before:
                out.append(_literal_body(before[-1]))
    if head.startswith("perl"):
        for m in _PERL_OPEN_RE.finditer(masked):
            out += _perl_open_commands(code, m.end(), head)
    if head.startswith("ruby"):
        for m in _RUBY_SEND_RE.finditer(masked):
            cmd = _first_argument_command(code, m.end(), head)
            if cmd:
                out.append(cmd)
    return out


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
# groups are unavailable anyway: they need 3.11, above the floor that
# pyproject.toml declares). DO NOT reuse this token UNGATED; if it is ever
# needed ungated, bound the path segments `(?:[^\s)/]*/){0,K}` (the F1
# mechanism), which caps the per-offset scan.
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
        if _shell_reads_stdin(preceding):
            return m.group(0)        # shell-fed heredoc: body executes — keep
        return m.group(1) + "HEREDOC_BODY_EXCISED\n" + m.group(3)

    return _HEREDOC_BODY_RE.sub(_repl, command)


def _delimiter_is_unescaped(excised: str, d: int) -> bool:
    """True iff the char at excised[d] is an UNescaped shell word-delimiter. Counts the
    consecutive backslash run ending at d-1: EVEN (incl. 0) → the delimiter is real; ODD →
    the delimiter is backslash-escaped (a literal char, part of the word) → NOT a delimiter.
    Real-bash discriminant (the design intent): with `echo "x"\\ #y | sh`, the escaped space
    is a literal argument char, the `#` is mid-word, and bash EXECUTES the pipe — so the
    routing view must keep it; with `echo "x" #y | sh` (unescaped) bash treats `#`..EOL as a
    comment and the pipe is SUPPRESSED — so excision is correct. `\\\\ #` (even run: literal
    backslash then a real space) is a comment again, matching bash."""
    b = d - 1
    bs = 0
    while b >= 0 and excised[b] == "\\":
        bs += 1
        b -= 1
    return bs % 2 == 0


def _excise_comments_view(excised: str, view: str) -> str:
    """Step 3 of _excise_and_mask: view-only, SAME-LENGTH comment excision. A `#`
    is a comment iff (a) it SURVIVES the quote mask (view[i] == "#": unquoted by
    construction) AND (b) its predecessor ON THE PRE-MASK SURFACE (excised[i-1];
    i == 0 counts as a comment start) is one of {start, space, tab, newline, ;,
    &, |} AND (c) that delimiter is UNESCAPED (_delimiter_is_unescaped: even
    backslash run immediately before it — `\\ #` / `\\<tab>#` / `\\;#` / `\\&#` /
    `\\|#` make the `#` mid-word, NOT a comment, and bash EXECUTES what follows).
    Testing the RAW predecessor is load-bearing: a masked closing quote
    reads as a space on the view, so a view-side test would misclassify
    `"x"#tail` as a comment (an executing tail eaten = under-block). The excision
    replaces `#`..end-of-line (newline EXCLUDED) with spaces — same length, so
    both consumers' 1:1 view/excised offset alignment holds (_procsub_anchor_view
    indexes the pair pairwise). FAIL-TOWARD-NOT-COMMENT: any predecessor outside
    the set leaves the text visible (worst case a residual over-block, never an
    under-block). The pinned predecessor set is a strict SUBSET of bash's
    comment-start contexts (bash also comments after `(`, backtick, …): fewer
    excisions than bash = fail-toward-not-comment relative to ground truth — do
    not widen without cert rows. Mask-BEFORE-excise gives quote-awareness for
    free: a `#` inside a balanced quoted span is already spaces in `view`, so a
    quoted `# … | sh` payload can never be excised through this step."""
    if "#" not in view:                       # cheap short-circuit
        return view
    out = list(view)
    i, n = 0, len(view)
    while i < n:
        if view[i] == "#" and (
            i == 0
            or (
                excised[i - 1] in " \t\n;&|"
                and _delimiter_is_unescaped(excised, i - 1)
            )
        ):
            j = view.find("\n", i)
            if j == -1:
                j = n
            for k in range(i, j):
                out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def _excise_and_mask(command: str) -> tuple[str, str]:
    """Shared prefix for the two routing-flag views (#1129 R3): excise heredoc
    bodies (opener line + closing marker kept; shell-fed bodies preserved), then
    space-mask every balanced quoted span via _mask_shell_quotes, then excise
    unquoted comments from the VIEW ONLY (a `#`-comment's `| sh` must not flip
    piped_to_shell and disable every carrier — the comment text never executes).
    Returns (excised, view). ORDER IS LOAD-BEARING: heredoc excision FIRST (stray
    body quotes desync the quote mask — and a `#` inside a heredoc body is gone
    before step 3 runs); mask BEFORE comment excision ("survives the mask" IS the
    quote-awareness — a pre-mask excision on the raw string would eat executing
    `' | sh'` tails through a quoted `#`). Steps 2 and 3 are both SAME-LENGTH, so
    view and excised align 1:1 by offset."""
    excised = _excise_heredoc_bodies_for_routing_scan(command)
    view = _mask_shell_quotes(excised)
    view = _excise_comments_view(excised, view)
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


def _strip_inert_dq_value(m: "re.Match") -> str:
    """Replace an inert double-quoted positional value with the bareword ``STRIPPED``;
    span-scope a value that contains ``$()``/backtick (executing spans preserved VERBATIM
    in the SAME dq context, surrounding inert literal stripped); FAIL-SAFE preserve-whole on
    a span the scanner cannot resolve. Byte-identical to the echo/printf carrier's dq-arg
    strip; used by the general inert-default positional strip (#1178, carrier 10) so a
    non-flag-anchored quoted positional is stripped with the SAME $()-preserve + fail-safe
    discipline as the flag-anchored carriers."""
    if not _has_command_substitution(m.group(0)):
        return "STRIPPED"
    transformed = _preserve_substitution_spans(m.group(0))
    return m.group(0) if transformed is None else transformed


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


def _strip_shell_comments(text: str) -> str:
    """`text` without its shell comments: a `#` that starts a word (line start, after
    whitespace or `;`) outside quotes, to the end of its line. A `#` inside '…' or "…"
    is text, and a quote inside a comment opens nothing. An unterminated quote leaves
    the rest as text."""
    out: list[str] = []
    i, n, quote = 0, len(text), ""
    while i < n:
        c = text[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(text[i:i + 2])
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or text[i - 1].isspace() or text[i - 1] == ";"):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        out.append(c)
        i += 1
    return "".join(out)


# Words and marks that open or close a compound command on the pipeline view: a group,
# a subshell or command substitution, a loop body, an if branch, a case.
_COMPOUND_OPEN_RE = re.compile(r"(?<![\w$])\{(?=\s)|\(|(?<![\w-])(?:do|then|case)(?![\w-])")
_COMPOUND_CLOSE_RE = re.compile(r"(?<![\w$])\}|\)|(?<![\w-])(?:done|fi|esac)(?![\w-])")


def _output_reaches_shell_from(view: str, procsub_view: str, pos: int) -> bool | None:
    """Whether the output of the simple command at `pos` reaches a shell: on the pipeline
    view (quotes and heredoc bodies blanked), from `pos` to the next pipeline separator,
    a `| shell` / `| xargs … shell` or an output-side `>(shell)`. None when `pos` sits
    inside an open compound command, whose output a pipe after it may route; the caller
    then keeps the whole-command flags."""
    before = view[:pos]
    if len(_COMPOUND_OPEN_RE.findall(before)) > len(_COMPOUND_CLOSE_RE.findall(before)):
        return None
    sep = _PIPELINE_SEPARATOR_RE.search(view, pos)
    end = sep.start() if sep else len(view)
    return bool(
        _has_pipe_to_shell(view[pos:end])
        or _has_process_substitution_to_shell(procsub_view[pos:end])
    )


def _is_leg_command_word(text: str, start: int) -> bool:
    """Whether the word at `start` is the command word of its simple command: only
    assignments, `command` or `builtin` stand between it and the last separator."""
    segment = re.split(r"[;&|\n(){}]|(?<![\w-])(?:then|do|else)(?![\w-])", text[:start])[-1]
    return re.fullmatch(r"\s*(?:[A-Za-z_]\w*=\S*\s+)*(?:(?:command|builtin)\s+)?", segment) is not None


def _blank_unquoted_print_words(span: str) -> str:
    """An echo/printf span with each unquoted word blanked, keeping the verb and any word
    holding a command or process substitution (it runs), read whole. A `${…}` other than
    a plain name keeps the rest of the span."""
    verb = re.match(r"\S+\s+", span)
    if verb is None:
        return span                           # a bare verb prints nothing
    out = [verb.group(0)]
    i, n = verb.end(), len(span)
    while i < n:
        j = i
        while j < n and span[j].isspace():
            j += 1
        if j > i:
            out.append(span[i:j])
            i = j
            continue
        runs = False
        while j < n and not span[j].isspace():
            if span.startswith(("$(", "<(", ">("), j):
                _, j = _extract_dollar_paren(span, j)
                runs = True
            elif span.startswith("${", j):
                plain = re.match(r"\$\{[A-Za-z_]\w*\}", span[j:])
                j, runs = (j + plain.end(), runs) if plain else (n, True)
            elif span[j] == "`":
                _, j = _extract_backtick(span, j)
                runs = True
            else:
                j += 2 if span[j] == "\\" else 1
        j = min(j, n)
        out.append(span[i:j] if runs else "STRIPPED")
        i = j
    return "".join(out)


def _shell_reads_stdin(preceding: str) -> bool:
    """Whether the text right before a heredoc or here-string operator ends in a shell that
    may read its commands from stdin: `bash`, `bash -e`, `bash -s [args]`. A shell given -c
    runs that string instead, and reads the heredoc as its input data."""
    words = re.split(r"[;&|\n(]", preceding)[-1].split()
    at = max((k for k, w in enumerate(words) if re.match(r"^(?:.*/)?(?:bash|sh|zsh)$", w)), default=None)
    if at is None:
        return False
    for word in words[at + 1:]:
        if word == "--" or not word.startswith("-"):
            break
        if re.match(r"^-[A-Za-z]*c", word):
            return False
    return True


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
    #    The output-side guard is PER PIPELINE: a heredoc's body is routed to a shell
    #    only when its OWN command's output reaches one (`cat <<EOF | bash`), not
    #    because some other pipeline in the command pipes to a shell. A heredoc inside
    #    a compound command (group, subshell, loop, if, case) keeps the whole-command
    #    flags, since the compound's output may be piped.
    #    GUARD (eval): a command whose eval is fed by a heredoc keeps its bodies, so
    #    the approval for it mints from the command the eval runs.
    eval_fed = _has_eval_with_heredoc(command)
    routing_views = None

    def _strip_heredoc(match: re.Match) -> str:
        nonlocal routing_views
        # Check what command precedes the heredoc operator
        start = match.start()
        preceding = command[:start].rstrip()
        # If the preceding command is a shell interpreter, preserve content
        if _shell_reads_stdin(preceding):
            return match.group(0)  # Preserve — content executes
        if eval_fed:
            return match.group(0)  # Preserve — an eval runs this heredoc's text
        if routing_views is None:
            routing_views = (_pipeline_view(command)[0], _procsub_anchor_view(command))
        routed = _output_reaches_shell_from(*routing_views, start)
        if routed is None:
            routed = piped_to_shell or process_sub_to_shell
        if routed:
            return match.group(0)  # Preserve — this heredoc's output runs in a shell
        return "<<HEREDOC_STRIPPED"

    result = re.sub(
        r"<<-?\s*['\"]?(\w+)['\"]?.*?\n.*?\n\t*\1\b",
        _strip_heredoc,
        result,
        flags=re.DOTALL,
    )

    # 2. Strip comments: # to end of line
    #    Only strip when # appears at start of line or after whitespace/semicolon
    #    (not inside words like issue#42 or URLs with #fragment), and outside quotes
    #    (`git commit -m 'fix #12' && …` keeps everything after the message).
    result = _strip_shell_comments(result)

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
        # echo/printf PRINT their args, they never execute them (#1140). Strip / span-scope
        # EVERY positional quoted arg, not just the first: a danger literal in a 2nd+ arg is
        # inert (printed) yet was over-blocking a faithful click. The ONLY execution path is
        # output-routing to a shell (`| bash`, `> >(bash)`), already handled by the outer
        # piped/process-sub skip this whole block sits inside — so widening to ALL args is
        # under-block-safe.
        def _strip_echo_dq_arg(m: re.Match) -> str:
            # One double-quoted arg. Inert -> bareword STRIPPED; a value with $()/backtick is
            # span-scoped (inert literal stripped, executing spans preserved in the SAME dq
            # context as base so detection is identical); fail-safe preserves whole.
            if not _has_command_substitution(m.group(0)):
                return "STRIPPED"
            transformed = _preserve_substitution_spans(m.group(0))
            return m.group(0) if transformed is None else transformed

        echo_text = result

        def _strip_echo_span(span_match: re.Match) -> str:
            # Strip every quoted arg within the echo/printf span. dq FIRST, then sq, so a dq
            # value's embedded `'` is consumed before the sq pass runs (no cross-contamination);
            # sq -> bareword STRIPPED (sq $() is literal). The verb + flags sit OUTSIDE these
            # inner subs and stay intact.
            span = span_match.group(0)
            # Inside php code, `echo` is php's statement, not the shell builtin:
            # `php -r 'echo shell_exec("…");'` RUNS its argument. Leave such a span whole
            # for the interpreter clause of carrier 10 to judge. Other interpreters have
            # no `echo` statement, so an `echo` in their quoted text is data.
            if _in_php_exec_code(echo_text, span_match.start()):
                return span
            span = re.sub(r'"(?:[^"\\]|\\.)*"', _strip_echo_dq_arg, span)
            span = re.sub(r"'[^']*'", "STRIPPED", span)
            # An echo/printf that is its leg's command prints its unquoted words too. Not
            # when the word only follows `echo` inside another command (`env X=echo gh …`).
            if _is_leg_command_word(echo_text, span_match.start()):
                span = _blank_unquoted_print_words(span)
            return span

        # Span = verb + the shared quote-aware body that STOPS at the first UNQUOTED
        # ;/&&/|/newline, so an executing tail (`echo "x" && git branch -D y`) stays OUTSIDE the
        # span and is caught (leg-locality). Keep the `\s+` so a bare `echo` token never matches.
        result = re.sub(
            r"\b(echo|printf)\s+" + _VERB_MSG_BODY,
            _strip_echo_span,
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
            var_name = match.group(1)
            if _is_graphql_merge_query(match):
                return match.group(0)  # Preserve — the gh api graphql request runs it
            # ORDER LOAD-BEARING: a bare $VAR/${VAR} expansion word-splits and EXECUTES the
            # WHOLE value (the inert literal INCLUDED, not just a substitution), so an expanded
            # var preserves WHOLE — this MUST precede the substitution span-scope below.
            if _var_is_expanded(var_name, command):
                return match.group(0)  # Preserve — $VAR executes the whole value
            if _has_command_substitution(match.group(0)):
                # `git -c section.key=value` config injection is OUT OF SCOPE (#11): a git config
                # value can be an EXECUTABLE config (core.pager / alias.* / core.editor), so its
                # literal is not provably inert — preserve WHOLE to keep the -c surface at its
                # exact status quo. Tell: a git config key ALWAYS has a `.`, so the matched key
                # segment is preceded by `.`; a shell var / `--flag=` name never is. Use
                # match.string (the post-carrier-1..3 result being sub'd), NOT `command` — the
                # match offsets index into that result, not the raw command.
                if match.start() > 0 and match.string[match.start() - 1] == ".":
                    return match.group(0)  # config injection — status-quo preserve
                # Non-expanded, non-config value WITH a substitution: strip the inert literal but
                # preserve the genuine $()/backtick spans (they execute) via the same certified
                # span scanner the message carriers use. A benign $(date) beside danger-looking
                # prose no longer reverts the whole value (the equals-form over-block cure).
                value = match.group(0)[len(var_name) + 1:]  # text after `NAME=`
                transformed = _preserve_substitution_spans(value)
                if transformed is None:
                    return match.group(0)  # FAIL-SAFE: preserve whole (exotic span)
                return var_name + "=" + transformed
            return var_name + "=STRIPPED"

        result = re.sub(
            r'\b([A-Za-z_][A-Za-z0-9_]*)="(?:[^"\\]|\\.)*"',
            _strip_var_dq,
            result,
        )

        # Single-quoted: guard against bare expansion
        def _strip_var_sq(match: re.Match) -> str:
            var_name = match.group(1)
            if _is_graphql_merge_query(match):
                return match.group(0)  # Preserve — the gh api graphql request runs it
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
                # --trailer (#1178) carries a commit TRAILER (`Key: value` metadata
                # appended to the message) — inert prose git never executes, so a
                # danger-looking literal in a trailer value must not gate a faithful
                # click. FLAG-anchored to the --trailer VALUE only (never a bare token),
                # exactly like --message; disjoint from the --m* arm (t != m after --),
                # so it can never over-strip another option's value. The `--trailer=VALUE`
                # equals form is already stripped upstream by the variable-assignment
                # carrier (which matches `trailer=...` as a NAME=VALUE), so only the SPACE
                # form reaches here.
                # Short cluster arm TOKEN-START anchored `(?<!\S)` (DEFENSIVE-CONSISTENCY
                # mirror of _MSG_FLAG_ANCHOR): genuine `-m`/`-am`/attached `-mMSG` are
                # whitespace-preceded so still strip; the anchor only blocks a MID-FLAG
                # mis-match (`-com`/`-adm`/`-ame`) that a future strip-order shift could
                # turn into an over-block. No census over-block runs through this carrier
                # today; the anchor keeps carrier-5 identical to the merge anchor's fix.
                r"((?:--m(?:e(?:s(?:s(?:a(?:g(?:e)?)?)?)?)?)?|--trailer|(?<!\S)-[a-ln-zA-Z]*m)\s*)",
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
            # Check what command precedes the <<< (shell-interpreter guard stays FIRST).
            start = match.start()
            preceding = command[:start].rstrip()
            if _shell_reads_stdin(preceding):
                return match.group(0)  # Preserve — a shell reads the here-string as stdin
            if not _has_command_substitution(match.group(0)):
                return "<<<STRIPPED"                  # no-$() unchanged (bareword)
            # has-$(): span-scope the value — strip the inert literal, preserve the executing
            # spans (#1140). Emit the scanner's native dq output: it keeps each preserved span
            # in the SAME executing dq context the coarse preserve used, so a real $(...) is
            # detected IDENTICALLY to base (verified by the bidirectional cert). group(1) = dq value.
            transformed = _preserve_substitution_spans(match.group(1))
            if transformed is None:
                return match.group(0)                 # fail-safe: preserve whole
            return "<<<" + transformed

        result = re.sub(
            r'<<<\s*("(?:[^"\\]|\\.)*")',
            _strip_herestring_dq,
            result,
        )

        def _strip_herestring_sq(match: re.Match) -> str:
            # Check what command precedes the <<<
            start = match.start()
            preceding = command[:start].rstrip()
            if _shell_reads_stdin(preceding):
                return match.group(0)  # Preserve — content executes
            return "<<<STRIPPED"

        result = re.sub(
            r"<<<\s*'[^']*'",
            _strip_herestring_sq,
            result,
        )

    # 6b. find -name/-path VALUE carrier: find's match-pattern values are inert
    #     (find matches names; it never executes a -name/-path value), so a
    #     danger-looking quoted pattern (`find . -name 'gh pr merge…'`) must not
    #     gate a faithful search click. Span-bounded like carrier 5 (the
    #     structural template): _VERB_MSG_BODY stops at the first unquoted
    #     ;/&&/|/newline, so an executing tail stays OUTSIDE the span and is
    #     caught. An executing find PRIMARY (-exec/-execdir/-ok/-okdir) anywhere
    #     in the span disables the strip entirely (fail-toward-preserve: the
    #     stripped value could be the -exec payload's evidence). Vocabulary is
    #     the match-pattern primaries -name/-path/-iname/-ipath/-regex/-iregex
    #     (#1195 OBS-A2 broadening — all inert: find MATCHES the pattern, never
    #     shell-executes it). The ONE executing primary family
    #     (-exec/-execdir/-ok/-okdir) stays whole-span-preserved (the deny below
    #     fires first). find itself stays in _EXEC_WRAPPERS_COARSE, so carrier 10
    #     still preserves find legs wholesale; the danger literal is simply gone
    #     from the match-pattern value by the time the floor scans.
    if not piped_to_shell and not process_sub_to_shell:
        _find_exec_primary_re = re.compile(r"(?<!\S)-(?:exec|execdir|ok|okdir)(?!\S)")
        _find_span = r"\bfind\s+" + _VERB_MSG_BODY

        def _strip_find_span(m: "re.Match") -> str:
            span = m.group(0)
            if _find_exec_primary_re.search(span):
                return span                   # -exec/-ok present => preserve whole span
            return _strip_flag_values(
                span,
                r"((?<!\S)-(?:name|path|iname|ipath|regex|iregex)\s+)",
                _keep_carrier_value,
            )

        result = re.sub(_find_span, _strip_find_span, result)

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
            # --assignee/-a (create) + --add-assignee/--remove-assignee (edit) carry a
            # GitHub LOGIN value (#1178) — inert API metadata never executed by a shell,
            # so a danger-looking literal in an assignee value must not gate a faithful
            # click. VALUE-taking on every carrier-7 verb (login arg), so they join the
            # value-strip set; -a is admissible here because within these issue/pr verbs
            # -a is unambiguously --assignee (contrast release -a=--attach, carrier 7b —
            # a DIFFERENT span, never matched by this gh issue/pr anchor).
            return _strip_flag_values(
                span_match.group(0),
                r"((?:--title|--body|--assignee|--add-assignee|--remove-assignee|-t|-b|-a)\s+)",
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
        # LAZY prefix gobbler `{0,N}?` (#1195 OBS-A3g) — SCOPED TO git-merge ONLY. "merge"
        # is uniquely BOTH a carrier verb AND a substring of the `gh pr merge` danger
        # pattern, so a GREEDY `{0,N}` gobbler crosses into a `-m` message VALUE that
        # contains "merge" (`git merge -m 'gh pr merge 5 --admin'`) and anchors the span on
        # that INNER merge, stripping from the wrong offset -> the danger literal survives
        # (over-block). Lazy matches the FIRST (VERB) merge, so _VERB_MSG_BODY spans the real
        # -m value and it strips cleanly. Only WHERE `merge` is matched changes; the
        # _VERB_MSG_BODY leg-locality terminator (stops at the first unquoted ;/&&/|/newline)
        # is UNCHANGED, so a real destructive op in a SEPARATE leg is never swallowed.
        # commit/tag/stash spans stay GREEDY — no danger pattern contains their verb word.
        _git_merge_span = (
            r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}?merge\b" % _MAX_GLOBAL_FLAG_TOKENS
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
            # GRAPHQL MERGE EXCEPTION: a gh api graphql query that calls a merge mutation
            # IS the request, so the query value is kept for the read floor. A branch merge
            # carries its target (`base=`) in the body, so its span is kept too.
            if _graphql_merge_mutation(span) is not None or _gh_api_branch_merge_leg(span):
                return span

            def _keep_flag_dq(m: re.Match) -> str:
                # group(1) = the flag (+ key= for form b) up to the opening quote.
                if not _has_command_substitution(m.group(0)):
                    return m.group(1) + "'STRIPPED'"  # no-$() unchanged (single-quoted)
                # has-$(): span-scope the value after the flag — strip the inert literal,
                # preserve the executing $()/backtick spans (#1140). Emit the scanner's native
                # dq output (NOT sq): it keeps each preserved span in the SAME executing dq
                # context the coarse preserve used, so a real $(...) is detected IDENTICALLY to
                # base (verified by the bidirectional cert).
                flag = m.group(1)
                transformed = _preserve_substitution_spans(m.group(0)[len(flag):])
                if transformed is None:
                    return m.group(0)                 # fail-safe: preserve whole
                return flag + transformed

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
            # Same shape as carrier 8's _keep_flag_dq; both are keep_fn to _strip_flag_values.
            if not _has_command_substitution(m.group(0)):
                return m.group(1) + "'STRIPPED'"      # no-$() unchanged (single-quoted)
            # has-$(): span-scope the value after the flag — strip the inert literal, preserve
            # the executing $()/backtick spans (#1140). Emit the scanner's native dq output (NOT
            # sq): it keeps each preserved span in the SAME executing dq context the coarse
            # preserve used, so a real $(...) is detected IDENTICALLY to base (verified by cert).
            flag = m.group(1)
            transformed = _preserve_substitution_spans(m.group(0)[len(flag):])
            if transformed is None:
                return m.group(0)                     # fail-safe: preserve whole
            return flag + transformed

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

    # 10. GENERAL inert-default positional strip (#1178) — the strip-inert-by-default
    #     inversion. For each shell-operator leg whose head is NEITHER a shell-string executor
    #     / modeled wrapper (a) NOR an http-client (b), AND whose masked surface is NOT itself
    #     dangerous (c), the quoted POSITIONAL values are INERT (POSIX argv of an unrecognized
    #     command is never executed by the outer shell), so a danger-looking literal there must
    #     not gate a faithful click — strip them (dq span-scopes $()/backtick; sq → bareword)
    #     while PRESERVING executor / http-client / dangerous legs verbatim so every executing
    #     arg stays caught and every destructive target survives for mint/read binding.
    #     MONOTONIC toward False (only removes inert quoted content; never synthesizes a danger
    #     token) so it can only CLOSE an over-block, never open one. Runs LAST (after the
    #     flag-anchored carriers 1–9 have stripped their prose) under the SAME output-routing
    #     skip. Leg slicing reuses the _slice_stripped_legs substrate on the in-progress result
    #     (NOT _split_into_legs → re-strip recursion), rejoined with the ORIGINAL separators so
    #     downstream leg boundaries are byte-unchanged.
    if not piped_to_shell and not process_sub_to_shell:
        result = _strip_inert_positional_legs(result)

    return result


# The eval forms whose input the guard can read: `cat` (or `cat -`) emitting exactly one
# heredoc, inside the eval's own substitution (i), feeding the eval's command (ii), or piped
# into it (iii). `q` is the substitution's double quote, when it has one.
_HEREDOC_OPENER = r"<<(?P<dash>-?)[ \t]*(?P<dq>['\"\\]?)(?P<tag>\w+)['\"]?"
_CAT = r"cat(?:[ \t]+-)?[ \t]*"
_READABLE_EVAL_RES = (
    re.compile(
        r"(?<![\w-])eval[ \t]+(?P<q>\"?)\$\([ \t]*" + _CAT + _HEREDOC_OPENER
        + r"[ \t]*\n(?P<body>.*?)\n(?P=tag)[ \t]*\n[ \t]*\)(?P=q)",
        re.DOTALL,
    ),
    re.compile(
        r"(?<![\w-])eval[ \t]+(?P<q>\"?)`[ \t]*" + _CAT + _HEREDOC_OPENER
        + r"[ \t]*\n(?P<body>.*?)\n(?P=tag)[ \t]*\n[ \t]*`(?P=q)",
        re.DOTALL,
    ),
    re.compile(
        r"(?<![\w-])eval[ \t]+(?P<q>\"?)\$\([ \t]*" + _CAT + r"\)(?P=q)[ \t]*" + _HEREDOC_OPENER
        + r"[ \t]*\n(?P<body>.*?)\n(?P=tag)(?=[ \t]*(?:\n|$))",
        re.DOTALL,
    ),
    re.compile(
        r"(?<![\w-])" + _CAT + _HEREDOC_OPENER + r"[ \t]*\|[ \t]*eval[ \t]+(?P<q>\"?)\$\([ \t]*"
        + _CAT + r"\)(?P=q)[ \t]*\n(?P<body>.*?)\n(?P=tag)(?=[ \t]*(?:\n|$))",
        re.DOTALL,
    ),
)


def _unquoted_heredoc_text(body: str) -> tuple[str, bool] | None:
    """The text an unquoted-delimiter heredoc body gives when its only expansions are
    backslash escapes (of `$`, a backtick, a backslash, a newline) and plain `$NAME` /
    `${NAME}` parameters, which stay as written, and whether it holds such a parameter;
    None for any other expansion."""
    out, i, n, params = [], 0, len(body), False
    while i < n:
        c = body[i]
        if c == "\\" and i + 1 < n and body[i + 1] in "$`\\\n":
            out.append("" if body[i + 1] == "\n" else body[i + 1])
            i += 2
            continue
        if c == "`":
            return None
        if c == "$":
            param = re.match(r"\$(?:[A-Za-z_]\w*|\{[A-Za-z_]\w*\})", body[i:])
            if param is None:
                return None
            out.append(param.group(0))
            params = True
            i += param.end()
            continue
        out.append(c)
        i += 1
    return "".join(out), params


def _rewrite_readable_evals(command: str) -> str:
    """`command` with each readable eval replaced by the text it runs: the body as written
    when the substitution is double-quoted, or its words joined by single spaces when it is
    unquoted (word splitting). Under an unquoted delimiter the body is read as the shell
    expands it: a backslash escape gives its character and a plain `$NAME` stays as written.
    Left as it is, so refused: such a body holding any other expansion (a substitution,
    `${…}` with an operator, arithmetic), or a plain parameter while the text it reads as is
    not guarded on its own; a body holding its own terminator line, where
    the shell ends the heredoc first; an unquoted substitution whose body holds a glob
    character, which the shell may expand to file names."""
    if "eval" not in command or "<<" not in command:
        return command

    def _executed(m: re.Match) -> str:
        body = m.group("body")
        if not m.group("dq"):
            read = _unquoted_heredoc_text(body)
            # A parameter the heredoc expands is parsed again by eval, so its value could be
            # code: such a body is read only when what it reads as is guarded on its own.
            if read is None or (read[1] and detect_command_operation_type(read[0]) is None):
                return m.group(0)
            body = read[0]
        tab = "\t" if m.group("dash") else ""
        if any(line.lstrip(tab) == m.group("tag") for line in body.split("\n")):
            return m.group(0)
        if not m.group("q") and any(ch in body for ch in "*?["):
            return m.group(0)
        return body if m.group("q") else " ".join(body.split())

    for pattern in _READABLE_EVAL_RES:
        command = pattern.sub(_executed, command)
    return command


def rewritten_executed_command(command: str) -> str | None:
    """The command as the guard judges it after its rewrites (each readable eval, and each
    run of a file written earlier, replaced in place by the text it runs, then each literal
    variable expanded; a script given a whole guarded command as one argument read as that
    command), or None when nothing was rewritten. When nothing else is rewritten, a value a
    literal variable keeps executing at a site the guard cannot read (an unknown program's
    argument) is reported as the command: the value itself, whole. The approval side
    reads it so the refusal and the approval share one rewrite. Never raises."""
    try:
        # A script given a whole guarded command, literally or through a literal variable,
        # is judged as that command before a file run's rewrite drops its arguments.
        script = (_script_argv_guarded_command(command)
                  or _script_argv_guarded_command(_expand_literal_variables(command)))
        if script is not None:
            return script
        rewritten = _rewrite_file_runs(_rewrite_readable_evals(command))
        kept: list[str] = []
        expanded = _expand_literal_variables(rewritten, kept)
        if expanded == rewritten and kept:
            return "; ".join(dict.fromkeys(kept))
        rewritten = _script_argv_guarded_command(expanded) or expanded
    except Exception:  # noqa: BLE001 - a reader for the approval side must not raise
        return None
    return rewritten if rewritten != command else None


def unreadable_evald_heredoc(command: str) -> bool:
    """True when an eval fed by a heredoc runs a body the guard cannot read, so the command
    is refused on that alone, and nothing was rewritten (`rewritten_executed_command` is
    None). The approval side binds such a command by its exact text. Never raises."""
    try:
        return _has_eval_with_heredoc(command) and rewritten_executed_command(command) is None
    except Exception:  # noqa: BLE001 - a reader for the approval side must not raise
        return False


# A file written and then run in the same command. Writers whose text the guard can read:
# echo, printf (`%s`/`%b` formats), `… | tee [-a] F` after one of those, and a heredoc fed to
# `cat` redirected to F (`cat > F <<TAG`, `cat <<TAG > F`) or to `tee [-a] F <<TAG`.
_FILE_RUN_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_FILE_HEREDOC_WRITER_RE = re.compile(
    r"(?:^|(?<=[;&|\n]))[ \t]*(?P<head>cat|tee)(?![\w-])(?P<before>[^\n<]*)"
    r"<<(?P<dash>-?)[ \t]*(?P<q>['\"\\]?)(?P<tag>\w+)['\"]?(?P<after>[^\n]*)\n"
    r"(?P<body>.*?)\n?(?P=tag)(?=[ \t]*(?:\n|$))",
    re.DOTALL | re.MULTILINE,
)


def _heredoc_write_target(m: re.Match) -> tuple[str, str] | None:
    """(file, mode) a heredoc writer line writes: `cat`'s `>`/`>>` redirect, or `tee`'s
    first file argument (`>>` with -a). None when the line names no file this can read."""
    words = _shell_tokenize(m.group("before") + " " + m.group("after"))
    if not words:
        return None
    if m.group("head") == "cat":
        for k, word in enumerate(words):
            redirect = re.match(r"^(>>?)(.*)$", word)
            if redirect:
                target = redirect.group(2) or (words[k + 1] if k + 1 < len(words) else "")
                return (target, redirect.group(1)) if target else None
        return None
    files = [w for k, w in enumerate(words)
             if not w.startswith("-") and not re.match(r"^\d*>", w)
             and not (k and re.match(r"^\d*>>?$", words[k - 1]))]
    if not files:
        return None
    return files[0], ">>" if any(w in ("-a", "--append") for w in words) else ">"
_PRINTF_READABLE_FORMAT_RE = re.compile(r"(?:[^%\\]|%[sb]|\\[nt\\])*")


def _file_key(token: str) -> str:
    return token[2:] if token.startswith("./") else token


def _redirect_target(tokens: list) -> "tuple[list, str | None, str | None]":
    """The tokens without their output redirection, the redirected file and its mode
    (`>` or `>>`). A file descriptor redirect (`2>`, `>&2`) is left in place."""
    words, target, mode = [], None, None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        m = re.fullmatch(r"(>>?)(.*)", tok)
        if m:
            mode = m.group(1)
            target = m.group(2) or (tokens[i + 1] if i + 1 < len(tokens) else None)
            i += 1 if m.group(2) else 2
            continue
        words.append(tok)
        i += 1
    return words, target, mode


def _printed_text(words: list) -> str | None:
    """What an echo or printf leg prints, or None when the guard cannot read it."""
    if words[0] == "echo":
        args, newline, escapes = words[1:], True, False
        while args and re.fullmatch(r"-[neE]+", args[0]):
            newline = newline and "n" not in args[0]
            escapes = "e" in args[0] or (escapes and "E" not in args[0])
            args = args[1:]
        text = " ".join(args)
        if escapes:
            text = text.replace("\\n", "\n").replace("\\t", "\t")
        return text + ("\n" if newline else "")
    if len(words) < 2 or not _PRINTF_READABLE_FORMAT_RE.fullmatch(words[1]):
        return None
    fmt = words[1].replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")
    slots = len(re.findall(r"%[sb]", fmt))
    args = words[2:]
    if not slots:
        return fmt
    out = []
    for k in range(0, max(len(args), 1), slots):
        chunk = args[k:k + slots] + [""] * (slots - len(args[k:k + slots]))
        out.append(re.sub(r"%[sb]", lambda _m, it=iter(chunk): next(it), fmt))
    return "".join(out)


def _fill_substitutions(view: str) -> str:
    """`view` with each unquoted `$(…)` or backtick span filled with `_`, so legs slice
    around it as one word. Same length; an unterminated span fills the rest."""
    out, i, n = list(view), 0, len(view)
    while i < n:
        if view[i] == "\\":
            i += 2
            continue
        if view.startswith("$(", i):
            _, end = _extract_dollar_paren(view, i)
        elif view[i] == "`":
            _, end = _extract_backtick(view, i)
        else:
            i += 1
            continue
        out[i:end] = "_" * (end - i)
        i = end
    return "".join(out)


_FILE_RUN_TAG = "PACT_FILE_RUN"


def _rewrite_file_runs(command: str) -> str:
    """`command` with each run of a file it wrote earlier replaced, in place, by the text
    written to that file (`echo 'gh pr merge 5' > f; bash f` is judged as `gh pr merge 5`;
    an interpreter's run, `python3 f`, as its program read on stdin from a heredoc).
    Writes and runs count only in command position on the top level of the command; a
    `cd`, a compound command, or a leg the guard cannot tokenize leaves the command as it
    is. A write holding a substitution or an expansion is read as its literal text, so a
    guarded command written beside it is still judged where the file runs."""
    if ">" not in command and "tee" not in command:
        return command
    flat = list(command)
    writes_at: dict[int, tuple] = {}
    bodies: list[tuple[int, int]] = []
    for m in _FILE_HEREDOC_WRITER_RE.finditer(command):
        target = _heredoc_write_target(m)
        if target is None:
            continue
        bodies.append((m.start("body"), m.end("body")))
        body = m.group("body")
        if m.group("dash"):
            body = "\n".join(line.lstrip("\t") for line in body.split("\n"))
        writes_at[m.start("head")] = (_file_key(target[0]), target[1], body + "\n")
        start = command.index("\n", m.end("tag"))
        flat[start:m.end()] = " " * (m.end() - start)
    flat_text = "".join(flat)
    view = _fill_substitutions(_leg_slicing_view(flat_text))
    if _COMPOUND_OPEN_RE.search(view):
        return command
    legs, last, seps = [], 0, []
    for sep in _COMPOUND_OPS_RE.finditer(view):
        legs.append((last, sep.start()))
        seps.append(sep.group(0).strip())
        last = sep.end()
    legs.append((last, len(command)))
    seps.append("")
    files: dict[str, list] = {}
    replacements: list[tuple[int, int, str]] = []
    printed = None
    for k, (a, b) in enumerate(legs):
        text = flat_text[a:b]
        tokens = _shell_tokenize(text)
        if tokens is None:
            return command
        if not tokens:
            continue
        head = tokens[0]
        here = next((v for pos, v in writes_at.items() if a <= pos < b), None)
        if head == "cd":
            return command
        if here is not None:
            key, mode, body = here
            files[key] = ([] if mode == ">" else files.get(key, [])) + [body]
            printed = None
            continue
        if head in ("echo", "printf"):
            words, target, mode = _redirect_target(tokens)
            text_out = _printed_text(words)
            if target is not None:
                key = _file_key(target)
                files[key] = ([] if mode == ">" else files.get(key, [])) + [text_out]
                printed = None
            else:
                printed = text_out if seps[k] == "|" else None
            continue
        if head == "tee" and k and seps[k - 1] == "|":
            names = [t for t in tokens[1:] if not t.startswith("-")]
            append = any(t in ("-a", "--append") for t in tokens[1:])
            for name in names:
                key = _file_key(name)
                files[key] = (files.get(key, []) if append else []) + [printed]
            printed = None
            continue
        printed = None
        run_file, span, interpreter = None, (a, b), None
        if head in _FILE_RUN_SHELLS and len(tokens) == 3 and tokens[1] == "<":
            run_file = tokens[2]
        elif head in ("source", ".") and len(tokens) == 2:
            run_file = tokens[1]
        elif head == "cat" and len(tokens) == 2 and seps[k] == "|" and k + 1 < len(legs):
            nxt = _shell_tokenize(flat_text[legs[k + 1][0]:legs[k + 1][1]]) or []
            if len(nxt) == 1 and nxt[0] in _FILE_RUN_SHELLS:
                run_file, span = tokens[1], (a, legs[k + 1][1])
        elif len(tokens) == 1 and _file_key(head) in files:
            run_file = head
        elif head in _FILE_RUN_SHELLS or _INTERPRETER_HEADS.match(os.path.basename(head)):
            shell = head in _FILE_RUN_SHELLS
            script = _launched_script(tokens, _SHELL_INLINE_FLAG_RE if shell else _INLINE_PROGRAM_FLAG_RE)
            if script is not None:
                run_file = tokens[script]
                if shell and run_file.startswith("<") and len(run_file) > 1:
                    run_file = run_file[1:]
                interpreter = None if shell else head
        if run_file is None:
            continue
        written = files.get(_file_key(run_file))
        if written is None:
            continue
        readable = "".join(part for part in written if part is not None).rstrip("\n")
        if interpreter is not None:
            # An interpreter runs the file as its own program: judged as that program
            # read on stdin from a quoted heredoc, where the interpreter rules read it.
            if _FILE_RUN_TAG in readable.split("\n"):
                continue
            readable = f"{interpreter} <<'{_FILE_RUN_TAG}'\n{readable}\n{_FILE_RUN_TAG}"
            if span[1] < len(command):
                readable += "\n"
        lead = len(text) - len(text.lstrip())
        replacements.append((span[0] + lead, span[1], readable))
    if not replacements:
        return command
    # The text a heredoc wrote now stands where the file runs, so the heredoc's own body
    # is data: drop it, so the rewrite holds each command it runs once.
    replacements += [(start, end, "") for start, end in bodies]
    out = command
    for start, end, literal in sorted(replacements, reverse=True):
        out = out[:start] + literal + out[end:]
    return out


# A shell flag that gives the command on the command line (-c) or reads it from stdin (-s).
_SHELL_INLINE_FLAG_RE = re.compile(r"^-$|^-[A-Za-z]*[cs]")


# Interpreter flags known to take a value, by family: given separately (`-W ignore`) or
# attached (`-Wignore`, `--max-old-space-size=4096`). Any other flag takes none.
_VALUE_FLAGS = (
    (re.compile(r"^(?:python|pypy)"), ("-W", "-X")),
    (re.compile(r"^(?:node|nodejs)"), ("-r", "--require", "--max-old-space-size")),
    (re.compile(r"^ruby"), ("-I", "-r")),
    (re.compile(r"^perl"), ("-I", "-M", "-m")),
)


def _launched_script(tokens: list, inline_flag: re.Pattern) -> int | None:
    """The index of the script path an interpreter or shell launch runs: the first word
    after its flags, with the arguments after it. None when a flag gives the program
    inline or on stdin instead (`inline_flag`), or no word follows the flags. A known
    value-taking flag's value (`_VALUE_FLAGS`) is skipped; any other flag's separate
    value before the path is read as the path."""
    head = os.path.basename(tokens[0])
    flags = next((names for family, names in _VALUE_FLAGS if family.match(head)), ())
    k = 1
    while k < len(tokens):
        tok = tokens[k]
        if not tok.startswith("-"):
            return k
        if tok in flags:
            k += 2                            # the flag's separate value
            continue
        if any(tok.startswith(f) for f in flags):
            k += 1                            # the value attached
            continue
        if inline_flag.match(tok):
            return None
        k += 1
    return None


def _script_launch_args(tokens: list) -> int | None:
    """The index where a script-file launch's own arguments start, or None when `tokens`
    is no script-file launch: `./x`, `../x`, a path to a file with a script extension, or
    an interpreter given a script path (`_launched_script`)."""
    head = os.path.basename(tokens[0])
    if _INTERPRETER_HEADS.match(head):
        script = _launched_script(tokens, _INLINE_PROGRAM_FLAG_RE)
        if script is not None and _SCRIPT_PATH_RE.search(tokens[script]):
            return script + 1
        return None
    if tokens[0].startswith(("./", "../")) or ("/" in tokens[0] and _SCRIPT_PATH_RE.search(head)):
        return 1
    return None


def _script_argv_guarded_command(command: str, depth: int = 0) -> str | None:
    """The guarded command a script-file launch is given whole, as one argument
    (`./retry.sh "gh pr merge 5"`, `python3 retry.py "gh pr merge 5"`), or None. A
    script-file launch is `./x`, `../x`, a path to a file with a script extension, or an
    interpreter given a script path. The argument must itself be a guarded gh or git
    command; one that is a flag's value (`--pattern 'gh pr merge'`) or that only names a
    command in prose is not."""
    view = _leg_slicing_view(command)
    last, spans = 0, []
    for sep in _COMPOUND_OPS_RE.finditer(view):
        spans.append((last, sep.start()))
        last = sep.end()
    spans.append((last, len(command)))
    for a, b in spans:
        word = _leg_command_word(command[a:b])
        tokens = _shell_tokenize(word) if word else None
        if not tokens:
            continue
        start = _script_launch_args(tokens)
        args: list[str] = [] if start is None else tokens[start:]
        for k, arg in enumerate(args):
            if k and args[k - 1].startswith("-"):
                continue                      # a flag's value
            arg_tokens = _shell_tokenize(arg) or []
            if (arg_tokens and os.path.basename(arg_tokens[0]) in ("gh", "git")
                    and _is_dangerous_at_depth(arg, depth + 1)):
                return arg
    return None


def _eval_heredoc_verdict(command: str, depth: int) -> bool | None:
    """None when no eval runs a heredoc. Otherwise whether the command is dangerous with
    each readable eval (`_READABLE_EVAL_RES`) replaced, in place, by the text it runs: the
    body as written when the substitution is double-quoted, or its words joined by single
    spaces when it is unquoted (word splitting). Judging in place keeps the variables
    assigned earlier in the command and the output's routing. A body with an unquoted
    delimiter must not expand (`$`, backtick, backslash). Any eval fed by a heredoc the
    guard cannot read (another reader, a file, several heredocs) is refused."""
    if not _has_eval_with_heredoc(command):
        return None

    rewritten = _rewrite_readable_evals(command)
    if _has_eval_with_heredoc(rewritten):
        return True
    return _is_dangerous_at_depth(rewritten, depth + 1)


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

    Judged on the executed surface (_eval_surface_view): an ``eval`` word in
    command position, fed by a heredoc that also executes. The heredoc feeds
    the eval when it is (i) inside the eval's ``$(...)``/backtick argument,
    (ii) on the eval's own simple command (``eval "$(cat)" <<EOF``), (iii) on
    an earlier leg of the eval's pipeline (``cat <<EOF | eval "$(cat)"``), or
    (iv) written to a file earlier in the command (``cat > f <<EOF``, ``cat >>
    f <<``, ``tee f <<``) with the eval after it. An eval word that only sits
    in data (a quoted string, a comment, a non-shell heredoc body) never fires.
    An unbalanced substitution after an executed eval fires (fail closed).
    """
    if "eval" not in command or "<<" not in command:
        return False
    view = _eval_surface_view(command)
    for m in _EXECUTED_EVAL_RE.finditer(view):
        if _eval_is_fed_by_heredoc(view, m.start("eval"), m.end("eval")):
            return True
    return False


# An `eval` word in command position on the eval surface: at the start of a line,
# or after a command separator, `(`, a backtick or `$(`, optionally after `builtin`
# or `command`.
_EXECUTED_EVAL_RE = re.compile(
    r"(?:^|(?<=[;&|(\n`]))[ \t]*(?:(?:builtin|command)[ \t]+)*(?P<eval>eval)(?![\w-])",
    re.MULTILINE,
)

# A heredoc written to a file: `cat > F <<`, `cat >> F <<`, `tee F <<`, in any order
# of the redirect and the operator within one simple command.
_HEREDOC_TO_FILE_RE = re.compile(
    r"(?:^|(?<=[;&|(\n`]))[ \t]*(?:"
    r"cat\b[^\n;&|]*?>>?[ \t]*[^\s(&][^\n;&|]*?<<|cat\b[^\n;&|]*?<<[^\n;&|]*?>>?[ \t]*[^\s(&]"
    r"|tee\b[^\n;&|<]*?[ \t][^-\s<][^\n;&|]*?<<|tee\b[^\n;&|]*?<<[^\n;&|]*?[ \t][^-\s<]"
    r")",
    re.MULTILINE,
)


def _mask_quotes_keeping_substitutions(command: str) -> str:
    """_mask_shell_quotes, except that an unescaped ``$(...)`` or backtick span
    inside a DOUBLE-quoted span stays visible: the shell runs it. Same length as
    the input. A backslash-escaped ``\\$(`` or backtick inside double quotes is
    literal and stays masked; single-quoted text is masked whole."""
    masked = _mask_shell_quotes(command)
    if "$(" not in command and "`" not in command:
        return masked
    out = list(masked)
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = command.find("'", i + 1)
            if j == -1:
                break
            i = j + 1
            continue
        if c != '"':
            i += 1
            continue
        j = i + 1
        while j < n and command[j] != '"':
            if command[j] == "\\":
                j += 2
                continue
            if command[j] == "$" and j + 1 < n and command[j + 1] == "(":
                _, end = _extract_dollar_paren(command, j)
                end = min(end, n)
                out[j:end] = command[j:end]
                j = end
                continue
            if command[j] == "`":
                _, end = _extract_backtick(command, j)
                end = min(end, n)
                out[j:end] = command[j:end]
                j = end
                continue
            j += 1
        i = j + 1
    return "".join(out)


def _eval_surface_view(command: str) -> str:
    """The executed surface for the eval+heredoc rule: non-shell heredoc bodies
    excised (opener line and closing marker kept; a shell-fed body stays), quoted
    spans masked except the substitutions a double-quoted span runs, and comments
    excised. Offsets of the excised command and the view align 1:1."""
    excised = _excise_heredoc_bodies_for_routing_scan(command)
    view = _mask_quotes_keeping_substitutions(excised)
    return _excise_comments_view(excised, view)


def _eval_is_fed_by_heredoc(view: str, start: int, end: int) -> bool:
    """Whether the eval at view[start:end] is fed by an executed heredoc (arms
    i-iv of _has_eval_with_heredoc). Walks the eval's simple command: a
    substitution span is read whole, and the walk ends at an unquoted separator."""
    i, n = end, len(view)
    while i < n:
        c = view[i]
        if c == "\\":
            i += 2
            continue
        if c == "$" and i + 1 < n and view[i + 1] == "(":
            span, i = _extract_dollar_paren(view, i)
            if span is None or "<<" in span:
                return True          # (i), or an unbalanced span: fail closed
            continue
        if c == "`":
            span, i = _extract_backtick(view, i)
            if span is None or "<<" in span:
                return True
            continue
        if view.startswith("<<", i):
            return True              # (ii)
        if c in ";&|\n":
            break
        i += 1
    # (iii): a heredoc on an earlier leg of the eval's pipeline.
    k = start - 1
    while k >= 0 and view[k] in " \t":
        k -= 1
    while k >= 4 and view[k - 6:k + 1] in ("builtin", "command"):
        k -= 7
        while k >= 0 and view[k] in " \t":
            k -= 1
    if k >= 0 and view[k] == "|" and (k == 0 or view[k - 1] != "|"):
        leg_start = max(view.rfind(sep, 0, k) for sep in (";", "&", "\n", "||"))
        if "<<" in view[leg_start + 1:k]:
            return True
    # (iv): a heredoc written to a file earlier in the command.
    return bool(_HEREDOC_TO_FILE_RE.search(view, 0, start))


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
    """P0 (shell-semantic substrate SSOT): resolve bash line-continuations BEFORE
    tokenization. Bash-faithful semantics: `\\<newline>` is SPLICED — REMOVED entirely,
    gluing the surrounding text — NOT replaced with a space. A separating space
    survives ONLY when one was already adjacent: `gh pr close 5 \\<newline>-d` splices
    to `gh pr close 5 -d` (the space before the backslash survives, so `-d` stays a
    clean separate token — the flag-scan under-block the split fix addresses), while
    `merge\\<newline>5` glues to `merge5` (bash's printf-verified tokenization). This
    empty-join (vs the former `→ space`) is what makes the comment predicate see the
    REAL predecessor: `echo "…"\\<newline>#x` splices to `echo "…"#x`, so the `#` is
    glued to the closing quote (a NON-delimiter) = NOT a comment, closing the #1148
    line-continuation-splice comment under-block by construction. Routed through every
    floor + mint call site, so mint and read resolve continuations IDENTICALLY
    (mint==read by construction)."""
    return command.replace("\\\n", "")


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


# OBS-E (#1195) — the UNIFIED push-to-main destination-ref predicate. A push refspec whose
# DESTINATION ref is EXACTLY main/master (optionally `<src>:`-prefixed, optionally
# `refs/heads/`-prefixed), matched as a COMPLETE ref via .fullmatch on the refspec TOKEN.
# Replaces the inaccurate `(?:main|master)(?!:)\b` predicate that was wrong BOTH ways
# (prefix-matched main-release/main.foo/main@v1 = cardinal over-block; required main to
# START the token so feature:main/refs/heads/main = under-block). The trailing negative-
# lookahead forbids any ref-name continuation char (a COMPLETE ref); the `[^\s:+]` src
# class excludes a leading `+` so `+main` is NOT a push-to-main dst (it FORCES → OBS-F).
# SSOT: the ONE predicate the shared _flag_condition_danger_op push-to-main arm uses, which
# BOTH detect (_detect_op_pass fallback) AND the read floor (_stripped_surface_danger) call
# — mint==read by construction, no literal/DANGEROUS_PATTERNS copy left to drift.
_PUSH_MAIN_DST_RE = re.compile(
    r"(?:[^\s:+][^\s:]*:)?"   # optional <src>:  (src has no ':' and no leading '+')
    r"(?:refs/heads/)?"      # optional full-ref dst prefix
    r"(?:main|master)"       # the DESTINATION branch
    r"(?![\w./@+-])"         # ... as a COMPLETE ref: no continuation char follows
)

# Per-leg op filter. `_flag_condition_danger_op` is FIRST-LEG-ANCHORED, so any op it
# recognizes is LOST when the destructive command is not the first leg
# (`cd /repo && git push origin main`). The caller-level per-leg loops (in
# detect_command_operation_type + _stripped_surface_danger) restore ANY-LEG coverage by calling
# the SAME union arm per leg, filtered to these classes — a THIN caller, no second predicate.
# Reached ONLY after the existing pipeline returns None/False, so no command that ALREADY
# gates changes its verdict.
#
# ALL SIX union-arm classes are listed. The four delete/close classes were added to close a
# good-faith-reachable UNDER-BLOCK: `cd /repo && git push origin --delete feature` ran
# COMPLETELY UNGATED. Widening this FILTER — rather than adding per-leg LITERAL arms — is
# what keeps that fix over-block-free: literal arms were measured at 3 cardinal over-blocks
# (e.g. `git push origin feature -o "note: use --mirror for backups"`) versus 0 here, because
# the delete families need a positional, value-aware parse a regex cannot do. The filter reuses
# the parse the union arm already performs on an ISOLATED leg, so a quoted `--mirror` mention
# in a benign leg is never a match.
#
# Filtering both consumers on this ONE constant is what makes mint == read by construction:
# a class added here reaches detect and the read floor in the same edit, so they cannot drift.
#
# CAVEAT (leg precedence): both loops return on the FIRST leg whose op is in this tuple. On a
# compound carrying TWO ops from this set, an EARLIER leg now wins where a later one used to
# — e.g. `cd /repo && git branch -Df temp && git push origin main` classifies branch-delete,
# not push-to-main. Every such command has >=2 independently-dangerous legs (per-leg danger is
# evaluated on an ISOLATED leg, which is its own first leg, so the dangerous-leg count is
# unchanged by this filter), and a >=2-dangerous-leg compound is refused at the pair level
# regardless of which label it carries — so this re-labels a verdict that never authorizes.
_PER_LEG_OPS = (
    "push-to-main",
    "force-push",
    "branch-delete",
    "close",
    "remote-ref-delete",
    "remote-mass-delete",
)


def _flag_condition_danger_op(command: str) -> str | None:
    """P4 union arm: classify the FIRST EXECUTABLE LEG of `command` by a quote-aware
    NORMALIZED-FLAG danger CONDITION across every flag spelling, returning the
    op-class ("close" / "branch-delete" / "force-push" / "remote-ref-delete" /
    "remote-mass-delete" / "push-to-main") iff a condition fires, else None.
    FIRST-LEG-ANCHORED (extending the conservative-RECOGNITION posture to this arm).
    NOTE: callers ALSO invoke this per-leg for every class in `_PER_LEG_OPS`
    (detect_command_operation_type + _stripped_surface_danger) to catch a recognized op
    in a NON-first leg — this function itself stays first-leg-anchored; the per-leg reach
    is the callers'. That split is what makes per-leg coverage safe: each call still sees
    ONE isolated leg and derives flags AND positionals from it, so widening the caller's
    filter can never introduce the cross-leg flag leak.
    Every surface consulted here — the token list, the coarse-shape prefixes,
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
        # push-to-main via the UNIFIED refspec-DST predicate (#1195 OBS-E, supersedes the
        # OBS-D branch-token arm + the removed detect literals + DANGEROUS_PATTERNS copies).
        # fullmatch _PUSH_MAIN_DST_RE on positionals[1:] — the REFSPECS. positionals[0] is
        # the REMOTE and is DELIBERATELY SKIPPED: a remote literally NAMED 'main'
        # (`git push main feature`) must NOT gate (it is a push of 'feature' to a remote
        # called 'main', not a push to the main branch). _push_positionals skips interposed
        # `-o` push-option values (OBS-C/D machinery); the K bound keeps the flag walk
        # linear; the `push\s` reachability (single guarded match) keeps a glued
        # `git push--force …` non-command out. LAST in the push branch (force/delete/mass
        # classify above); SHARED arm ⇒ mint==read by construction.
        _pm = re.search(_GIT_PREFIX + r"push\s(.*)$", prefix)         # well-formed push only
        if _pm is not None:                                          #   (excludes push--force glue)
            _tail = _pm.group(1).split()
            if sum(1 for t in _tail if t.startswith("-")) <= _MAX_GLOBAL_FLAG_TOKENS:  # perf bound
                _refspecs = [_strip_surrounding_quotes(r) for r in _push_positionals(_tail)[1:]]
                # +<refspec> force-push (#1195 OBS-F): a leading '+' on ANY refspec is git's
                # documented FORCE spelling — symmetric with `--force` → force-push for any
                # target. Checked AFTER delete/mass (a `+:main` / `--delete +main` is a DELETE,
                # claimed above) and BEFORE push-to-main (a `+main` FORCES → force-push, never
                # push-to-main: a push-to-main token must NOT authorize a forced ref update;
                # `git push origin +feature main` = force-push). `_PUSH_MAIN_DST_RE.fullmatch`
                # is already False on a `+`-leading token (the src class excludes leading '+'),
                # so no conflict. Over-block-safe by construction: force-push already gates any
                # target; `+ref` is just another spelling. Bare `+` is not a refspec (len > 1).
                if any(r.startswith("+") and len(r) > 1 for r in _refspecs):
                    return "force-push"
                for _r in _refspecs:                                  # REFSPECS only (skip remote)
                    if _PUSH_MAIN_DST_RE.fullmatch(_r):
                        return "push-to-main"
    return None


# =============================================================================
# #1178 GENERAL inert-default positional strip — catalog, preserve-predicate,
# wrapper recursion, and the carrier-10 leg strip.
#
# The strip-inert-by-DEFAULT inversion (design of record, SOUND-BOUNDED): a leg whose head
# is an UNRECOGNIZED command has POSIX-argv-inert quoted positionals (the outer shell never
# executes them), so a danger-looking literal there must not gate a faithful click — strip
# it. A leg is PRESERVED (not stripped) iff its quoted content is load-bearing for a True
# verdict: (a) its head hands a quoted string to a shell/interpreter (executor), or wraps a
# command that does (modeled wrapper, RECURSED); (b) its head is an http-client whose
# destructive target is a quoted URL that masking hides; or (c) its masked surface is itself
# dangerous (a bare-token git/gh op whose quoted TARGET must survive mint/read binding). The
# strip is MONOTONIC toward False: it only ever REPLACES an inert quoted value with STRIPPED
# (or a $()-span-scoped value) — it never synthesizes a danger token — so it can only CLOSE
# an over-block (True->False), never OPEN one; the whole safety burden reduces to preserve-
# predicate completeness on the load-bearing legs.
# =============================================================================

# (a) Shell-string executors / interpreters. TOKEN-EQUALITY on the basename-normalized head
# (C1 CARDINAL — NEVER substring/\b: a substring scan sees `bash` in `bash-completion` /
# `python` in `python-config` and would wrongly PRESERVE an inert command's quoted arg,
# re-opening the over-block). Bounded per-FAMILY patterns fold version/alias variants into
# ONE member (python[0-9.]* / node|nodejs / ksh(93)?) so a real versioned interpreter is
# caught while distinct tokens (python-config, node-gyp) are NOT (^…$ anchors the whole
# token). busybox is a WRAPPER (its applet/executor is the 2nd token) → the wrapper set below.
_SHELL_STRING_EXECUTORS = re.compile(
    r"^(?:"
    r"sh|bash|zsh|dash|ash|fish|csh|tcsh"                # POSIX + common shells
    r"|ksh(?:93)?|rksh|mksh"                             # ksh family (ksh93 != ksh)
    r"|hush|lash|msh"                                   # busybox exec-shell applets (run a -c string)
    r"|eval|expect|watch"                               # string runners
    r"|ssh|rsh|remsh|slogin|su"                         # remote / user-switch (-c / a command)
    r")$"
)

# (a') Interpreters run their quoted text as program code, and that code reaches a shell only
# through an exec primitive of its language. A leg headed by one preserves its quoted values
# only when the leg text carries a primitive; without one, -c/-e code, a script's or -m
# module's argv and an awk program are inert. The primitive list is deliberately over-wide (a
# case-insensitive substring anywhere in the leg): a false match preserves, the over-block-safe
# direction. The heads are _INTERPRETER_HEAD_ALTERNATION.
_INTERPRETER_HEADS = re.compile(r"^(?:" + _INTERPRETER_HEAD_ALTERNATION + r")$")
# Every non-obfuscated route to a process from each interpreter names one of these:
#   python  os.system/popen, os.exec*/spawn*/posix_spawn*, subprocess (also asyncio's
#           create_subprocess_*), pty.spawn, commands.get*, os.fork
#   ruby    system, exec, spawn, backticks, %x, IO.popen, Open3, PTY.spawn, open("|cmd")
#   perl    system, exec, qx, backticks, readpipe, IPC::Open2/Open3/Run, open with a pipe
#   node    child_process (exec/execSync/spawn/fork/execFile), process.binding
#   php     system, exec, shell_exec, passthru, popen, proc_open, pcntl_exec, backticks
#   awk     system(), print | "cmd", "cmd" | getline, |& coprocesses
#   bun     Bun.spawn/spawnSync, Bun.$`cmd` (a backtick template), child_process
#   deno    Deno.run, new Deno.Command
#   lua     os.execute, io.popen
#   R       system, system2, pipe()
#   osascript  do shell script
# The reflective entries (eval, getattr, import_module, ...) catch an honest dynamic call.
# A primitive name built at runtime (string arithmetic inside a reflective call) is not
# matched: that needs deliberate obfuscation, an accepted adversarial-only residual.
_INTERPRETER_EXEC_PRIMITIVES = (
    "system", "exec", "spawn", "popen", "subprocess", "child_process", "open3", "open2",
    "passthru", "proc_open", "pty", "fork", "readpipe", "ipc::", "binding",
    "commands.get", "eval", "getattr", "__import__", "import_module", "compile", "vars",
    "globals", "locals", "__dict__", "builtins", "methodcaller", ".send(", "public_send",
    "call_user_func", "core::", "`", "qx", "%x", "|", "deno.run", "deno.command", "pipe(",
    "shell script",
)
_INTERPRETER_EXEC_PRIMITIVE_RE = re.compile(
    "|".join(re.escape(p) for p in _INTERPRETER_EXEC_PRIMITIVES), re.IGNORECASE
)

# (a) Modeled exec-WRAPPERS whose (possibly nested) executor's quoted arg executes. Split by
# grammar tractability. RECURSE: the wrapper's own args are a bounded, well-known skip from
# the head to the nested command, which _is_preserve_leg then RE-EVALUATES (nested executor →
# preserve, case A caught; nested inert → strip, case B freed). sudo is RECURSE (≈ doas + a
# bigger flag table; -i/-s/-e = shell/edit forms → preserve): sudo is ubiquitous and
# `sudo logger "…"` / `sudo mycmd "…"` over-blocks are COMMON, not contrived, so coarse-
# preserving it would retain a cardinal over-block. busybox + stdbuf are ALSO RECURSE: busybox's
# applet is simply token[1] (empty own-flag grammar → nested command = token[1..]), and stdbuf's
# only options are the -i/-o/-e buffering-mode VALUE-flags (attached `-oL` / separated `-o L` /
# long `--output=L`) skippable by the phased walk — so their wrapped-inert over-block is CLOSEABLE
# and MUST be closed. COARSE (still): grammars whose nested-command boundary is NOT reliably
# skippable — find's `-exec CMD` is `;`/`+`-terminated with `{}` placeholders; flock has a leading
# LOCKFILE positional + a `-c "STR"` string-exec arm (flock -c runs via sh -c) + FD forms —
# preserved WHOLE (over-block-safe: `flock -c "STR"` stays CAUGHT; the `flock /lock mycmd "…"`
# wrapped-inert over-block is a contrived, tracked residual). Enumerating EVERY wrapper is the
# #1098 wall (D1): an unmodeled custom wrapper's under-block is a tolerated residual. NOTE:
# recursing busybox requires its exec-capable shell applets (sh/ash/hush/lash/msh + awk) to be
# recognized preserve-heads — hush/lash/msh were added to _SHELL_STRING_EXECUTORS so
# `busybox hush -c "danger"` stays CAUGHT after the flip (else a fix-introduced under-block).
_EXEC_WRAPPERS_RECURSE = frozenset({
    "nohup", "setsid", "nice", "doas", "sudo", "timeout", "xargs", "env",
    # Nameable exec-prefix family (#1178, user ruling = Option X + RECURSION). All 13 nameable
    # exec-prefixes RECURSE (via _WRAPPER_GRAMMAR) so `<prefix> bash -c "danger"` stays CAUGHT
    # AND the faithful inert `<prefix> mycmd "danger"` FREES — the over-block is CLOSEABLE, so
    # it MUST be closed ("contrived" is not a valid exception under the user principle; only a
    # by-construction-unclosable residual like curl/wget is acceptable). Per-prefix arg-skip
    # grammar below, each BIDIRECTIONALLY certified (bash -c caught ∧ mycmd freed ∧ value-flag-
    # before-executor caught). The ONLY tolerated under-block residual is the genuinely-CUSTOM
    # (myrunner-style) exec-wrapper tail (ratified D1 — enumerating every wrapper = the #1098 wall).
    "time", "exec", "command", "chrt", "taskset", "ionice", "unbuffer",
    "proxychains", "torsocks", "catchsegv", "nocache", "eatmydata", "rlwrap",
    # busybox + stdbuf RECURSE (F2 closure): busybox = empty own-flag grammar, applet = token[1]
    # (its exec-capable shell applets sh/ash/hush/lash/msh + awk are recognized preserve-heads so
    # `busybox sh -c "danger"` stays caught while `busybox mycmd "danger"` frees); stdbuf = -i/-o/-e
    # buffering-mode VALUE-flags (attached `-oL` handled by the phased walk's short-value branch).
    "busybox", "stdbuf",
})
_EXEC_WRAPPERS_COARSE = frozenset({
    # Plan-ratified members whose nested-command boundary is NOT reliably skippable: find's -exec
    # CMD is ;/+-terminated with {} placeholders; flock has a lockfile positional + a -c string-exec
    # arm — both COARSE preserve-whole (their `<wrapper> bash -c "danger"` stays caught; the
    # wrapped-inert over-block is a contrived, tracked residual). busybox/stdbuf moved to RECURSE
    # (above, F2). The nameable exec-prefix family RECURSES, not here.
    "find", "flock",
})

# (b) http-client heads whose destructive target is a quoted URL that _mask_shell_quotes
# blanks (so the danger battery on the masked surface CANNOT see it) → preserving is the
# load-bearing #1098-consistent exclusion (a strip would UNDER-block). curl/wget matched by
# shlex basename head (catches a quoted "curl": base-True via \bcurl\b + a .* lookahead that
# spans the closing quote). gh api is matched on the masked surface via _GH_API_PREFIX (an
# unquoted `gh api`; a quoted "gh" api is base-FALSE — \bgh\s+ is broken by the closing quote
# — so it needs no preserve).
_HTTP_CLIENT_HEADS = frozenset({"curl", "wget"})

# Recognized git/gh TOOL heads. Carrier 10 PRESERVES these legs wholesale and DEFERS to their
# dedicated carriers (5 git commit / 7 gh issue-pr / 7b-e / 8 curl-body / 9 gh-api selector) +
# the danger floor: re-stripping a value those carriers intentionally preserved is an
# under-block (a git commit -m/tag $() fail-safe value, a `git -c` config dot-guard value, an
# expanded var), and a DANGEROUS git/gh leg's target must survive for binding. Non-carrier
# git/gh prose (e.g. `git log --grep "…git branch -D…"`) stays a benign over-block RESIDUAL —
# never an under-block. (curl/wget/gh-api are the http-client heads above / the gh-api masked
# clause, handled separately.)
_TOOL_HEADS = frozenset({"git", "gh"})


# =============================================================================
# READ-VERB value-strip vocabulary — the carve-out from the d2 _TOOL_HEADS
# preserve above. A git/gh READ verb (log/show/shortlog/grep, issue/pr list,
# search) never executes its search/filter VALUES, so a danger-looking quoted
# literal there (`git log --grep "gh pr merge 5 --admin"`) is an inert faithful
# click that must not gate — _strip_read_verb_values (below, pre-d2 in
# _maybe_strip_leg) strips exactly these values before the wholesale preserve
# can fire.
#
# STRUCTURE RULES (each load-bearing):
#   - Keys are the FULL verb path with EXACT token equality, never a shared
#     short-flag set: `-e` is a value-taking pattern on git grep but the
#     boolean `--email` on git shortlog — a shared table would mis-consume the
#     next real token (the curl/wget-shaped fail-open). The verb set is CLOSED
#     (design ruling): git diff and adjacents are OUT (git diff's harmless
#     `-O <orderfile>` vs git grep's EXECUTING `-O<pager>` is exactly why
#     per-verb keying is mandatory).
#   - flag_arm is ONE capturing group (the _strip_flag_values flag_sep
#     contract) whose alternatives are each anchored `(?<!\S)` (token start —
#     a `--not-grep`-style superset can never match) and ordered LONGEST-FIRST
#     (`--grep-reflog` before `--grep`; `--author-date` before `--author`).
#     Long flags take `\s+` (space form ONLY: the `--flag="…"` form is
#     carrier-4-pre-stripped upstream and MUST NOT be re-handled here); value
#     shorts take `\s*` (attached `-Sfoo`/`-S'foo'` or spaced `-S foo` — the
#     carrier-5 idiom). Bundled-cluster spellings (`-nS'x'`) are deliberately
#     NOT matched → preserved → status-quo residual.
#   - BOOLEANS ARE EXCLUDED BY OMISSION: with flag-anchored arms an unlisted
#     boolean is simply never touched, so the only enumeration burden is
#     "never list a boolean" (`--invert-grep`, `--all-match`, `-w/--web`,
#     `--draft`, shortlog `-e/--email` MUST NOT appear — listing an arity-0
#     flag would consume the next real token). Ambiguous arity → exclude
#     (fail-toward-preserve). `--format`/`--pretty` (git) stay OUT of every
#     flag_arm (smallest certified vocabulary; preserved = status quo), and
#     `--json`/`--jq`/`--template` (gh) are in the flag_arm of `gh pr view` and
#     `gh issue view` only: those two verbs never execute a value.
#   - deny_re: an EXECUTING flag anywhere in the leg's shlex tokens preserves
#     the WHOLE leg (fail-toward-preserve). git grep `-O[<pager>]` /
#     `--open-files-in-pager[=…]` EXECUTES its attached value (`-Ovim` runs
#     vim; bare `-O` runs the default pager) — deny all spellings; the
#     cluster-tolerant `-[a-zA-Z]*O` is deliberately over-wide (a false deny
#     only preserves, never under-blocks).
#   - strip_positionals (git grep / gh search only): after the guards in
#     _strip_read_verb_values, every remaining quoted span in such a leg is
#     data the verb never executes (patterns, pathspecs, query terms); gh
#     search's `--` sentinel needs no special code (tokens after `--` are
#     positionals and strip like any quoted span).
#
# Flag arities are doc-verified against git 2.50.1 (man git-log/git-grep/
# git-shortlog) and gh 2.96.0 (`gh <verb> --help`), per-subcommand for
# gh search (prs/issues/commits/repos/code differ; a prs-only flag like
# `-B/--base` never enters a sibling subcommand's arm).
# =============================================================================

class _ReadVerbSpec(NamedTuple):
    """Per-(tool, verb…) read-verb strip spec for _strip_read_verb_values."""
    flag_arm: "re.Pattern | None"   # ONE capturing group: the flag(+separator) to keep;
                                    # value consumed via _strip_flag_values' 3 arms
    deny_re: "re.Pattern | None"    # executing-flag deny — ANY token match => whole-leg preserve
    strip_positionals: bool         # grep/search only: strip remaining quoted spans


# git grep's ONLY executing flag family: -O[<pager>] / --open-files-in-pager[=<pager>]
# (attached-only optional value, EXECUTES it). Matched against quote-stripped shlex
# tokens (shell semantics: a quoted '-O' still reaches git as the flag).
_GIT_GREP_DENY_RE = re.compile(r"(?<!\S)-[a-zA-Z]*O|(?<!\S)--open-files-in-pager(?:=|\b)")

_READ_VERB_SPECS: "dict[tuple[str, ...], _ReadVerbSpec]" = {
    ("git", "log"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--grep-reflog|--grep|--author|--committer)\s+"
            # bundled-cluster pickaxe short (#1195 OBS-A2): a dash cluster ENDING in the
            # uppercase pickaxe flag S/G (`-nS'…'`, `-wG'…'`). [SG] uppercase-only so it
            # never mis-binds `-s`/lowercase; the value is an inert pickaxe string.
            r"|(?<!\S)-[a-zA-Z]*[SG]\s*)"
        ),
        None, False,
    ),
    ("git", "show"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--grep|--author|--committer)\s+"
            r"|(?<!\S)-[a-zA-Z]*[SG]\s*)"
        ),
        None, False,
    ),
    # NO shorts on shortlog: its `-e` is the boolean `--email` (the cross-verb
    # collision that mandates per-verb keying).
    ("git", "shortlog"): _ReadVerbSpec(
        re.compile(r"((?<!\S)(?:--grep|--author|--committer)\s+)"),
        None, False,
    ),
    ("git", "grep"): _ReadVerbSpec(
        re.compile(r"((?<!\S)-[ef]\s*)"),
        _GIT_GREP_DENY_RE, True,
    ),
    ("gh", "issue", "list"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--search|--author|--app|--assignee|--label|--milestone"
            r"|--mention|--type|--state|--limit|--repo)\s+"
            r"|(?<!\S)-[SAalmsLR]\s*)"
        ),
        None, False,
    ),
    # gh pr view / gh issue view never execute a value: --jq is a jq filter and --template a
    # Go template. The booleans -c/--comments and -w/--web are omitted; positionals stay.
    ("gh", "pr", "view"): _ReadVerbSpec(
        re.compile(r"((?<!\S)(?:--json|--jq|--template|--repo)\s+|(?<!\S)-[qtR]\s*)"),
        None, False,
    ),
    ("gh", "issue", "view"): _ReadVerbSpec(
        re.compile(r"((?<!\S)(?:--json|--jq|--template|--repo)\s+|(?<!\S)-[qtR]\s*)"),
        None, False,
    ),
    ("gh", "pr", "list"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--search|--author|--app|--assignee|--label|--base"
            r"|--head|--state|--limit|--repo)\s+"
            r"|(?<!\S)-[SAalBHsLR]\s*)"
        ),
        None, False,
    ),
    # gh search value-flag vocabularies are PER-SUBCOMMAND (gh 2.96.0 --help;
    # issues/commits/repos/code are NOT the prs superset — a superset-applied
    # flag that is boolean/absent on a sibling would mis-consume). Longest-first
    # where one flag prefixes another (--review-requested/--reviewed-by before
    # --review; --author-*/-committer-* before --author/--committer).
    ("gh", "search", "prs"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--app|--assignee|--author|--base|--checks|--closed"
            r"|--commenter|--comments|--created|--head|--interactions|--involves"
            r"|--label|--language|--limit|--match|--mentions|--merged-at"
            r"|--milestone|--order|--owner|--project|--reactions|--repo"
            r"|--review-requested|--reviewed-by|--review|--sort|--state"
            r"|--team-mentions|--updated|--visibility)\s+"
            r"|(?<!\S)-[BHLR]\s*)"
        ),
        None, True,
    ),
    ("gh", "search", "issues"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--app|--assignee|--author|--closed|--commenter"
            r"|--comments|--created|--interactions|--involves|--label"
            r"|--language|--limit|--match|--mentions|--milestone|--order"
            r"|--owner|--project|--reactions|--repo|--sort|--state"
            r"|--team-mentions|--updated|--visibility)\s+"
            r"|(?<!\S)-[LR]\s*)"
        ),
        None, True,
    ),
    ("gh", "search", "commits"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--author-date|--author-email|--author-name|--author"
            r"|--committer-date|--committer-email|--committer-name|--committer"
            r"|--hash|--limit|--order|--owner|--parent|--repo|--sort|--tree"
            r"|--visibility)\s+"
            r"|(?<!\S)-[LR]\s*)"
        ),
        None, True,
    ),
    ("gh", "search", "repos"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--created|--followers|--forks|--good-first-issues"
            r"|--help-wanted-issues|--include-forks|--language|--license"
            r"|--limit|--match|--number-topics|--order|--owner|--size|--sort"
            r"|--stars|--topic|--updated|--visibility)\s+"
            r"|(?<!\S)-L\s*)"
        ),
        None, True,
    ),
    ("gh", "search", "code"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--extension|--filename|--language|--limit|--match"
            r"|--owner|--repo|--size)\s+"
            r"|(?<!\S)-[LR]\s*)"
        ),
        None, True,
    ),
}


# Bounded recursion depth for nested wrappers (`timeout nice bash -c …`). Beyond this →
# fail-safe PRESERVE. 3 covers realistic nesting; the guard prevents pathological recursion.
_MAX_WRAPPER_DEPTH = 3

class _WrapperGrammar(NamedTuple):
    """Per-wrapper own-arg grammar for the recursion walk. bool_flags = flags to skip (token
    only); value_flags = flags whose NEXT token is a value to skip too; shell_flags = flags
    that invoke a shell / make the arg the command → PRESERVE the whole leg; positionals =
    count of leading NON-flag positional args to skip before the command (timeout's DURATION =
    1). Long =forms (`--flag=x`) collapse to one token; env NAME=VALUE is handled generically.
    An UNRECOGNIZED dash-flag (unknown arity) → PRESERVE — skipping the wrong number of tokens
    could land PAST the executor = the under-block the fail-safe forbids."""
    bool_flags: "frozenset[str]"
    value_flags: "frozenset[str]"
    shell_flags: "frozenset[str]"
    positionals: int


_WRAPPER_GRAMMAR: "dict[str, _WrapperGrammar]" = {
    "nohup":   _WrapperGrammar(frozenset(), frozenset(), frozenset(), 0),
    "setsid":  _WrapperGrammar(frozenset({"-c", "-f", "-w", "--ctty", "--fork", "--wait"}), frozenset(), frozenset(), 0),
    "nice":    _WrapperGrammar(frozenset(), frozenset({"-n", "--adjustment"}), frozenset(), 0),
    "doas":    _WrapperGrammar(frozenset({"-L", "-n"}), frozenset({"-C", "-u"}), frozenset({"-s"}), 0),
    "timeout": _WrapperGrammar(frozenset({"--preserve-status", "--foreground", "-v", "--verbose"}), frozenset({"-s", "--signal", "-k", "--kill-after"}), frozenset(), 1),
    "xargs":   _WrapperGrammar(frozenset({"-0", "--null", "-r", "--no-run-if-empty", "-t", "--verbose", "-p", "--interactive", "-x", "--exit", "-o", "--open-tty"}), frozenset({"-I", "-i", "-n", "--max-args", "-L", "--max-lines", "-P", "--max-procs", "-s", "--max-chars", "-d", "--delimiter", "-E", "-e", "-a", "--arg-file"}), frozenset(), 0),
    "env":     _WrapperGrammar(frozenset({"-i", "--ignore-environment", "-0", "--null", "-v"}), frozenset({"-u", "--unset", "-C", "--chdir"}), frozenset({"-S", "--split-string"}), 0),
    # sudo (≈ doas + a bigger table). shell/edit forms {-i,-s,-e} → preserve (no explicit CMD).
    # -h OMITTED (ambiguous help-vs-host) → unrecognized → preserve (over-block-safe). Value
    # flags (-u/-g/-C/-p/-R/-r/-T/-t/-U/-D) verified vs sudo(8). Arity errors here would
    # under-block, so the value/bool split is cert-pinned per Q2 (value-flag-before-executor).
    "sudo":    _WrapperGrammar(frozenset({"-A", "--askpass", "-B", "--bell", "-b", "--background", "-E", "--preserve-env", "-H", "--set-home", "-K", "--remove-timestamp", "-k", "--reset-timestamp", "-l", "--list", "-n", "--non-interactive", "-P", "--preserve-groups", "-S", "--stdin", "-V", "--version", "-v", "--validate"}), frozenset({"-C", "--close-from", "-D", "--chdir", "-g", "--group", "-p", "--prompt", "-R", "--chroot", "-r", "--role", "-T", "--command-timeout", "-t", "--type", "-U", "--other-user", "-u", "--user"}), frozenset({"-i", "--login", "-s", "--shell", "-e", "--edit"}), 0),
    # Nameable exec-prefixes (#1178 Option X, RECURSION). Each bidirectionally certified (bash -c
    # caught ∧ mycmd freed ∧ value-flag-before-executor caught). Unlisted flags → unrecognized →
    # PRESERVE (fail-safe: an unmodeled option over-blocks rather than mis-skips past the
    # executor). time = bash keyword (-p) + /usr/bin/time (-o/-f value). chrt/taskset carry a
    # leading PRIORITY/MASK positional + a -p/--pid operate-on-existing-PID form (no CMD → shell/
    # preserve). ionice: -c/-n class values. proxychains: -f config. torsocks: -d/-a/-p/-P/-u/-s
    # values. rlwrap: large value set (-b/-C/-D/-e/-f/-g/-H/-l/-O/-P/-q/-s/-S/-t/-w/-z); -a/-m/-p
    # are OPTIONAL-ATTACHED (bool unless attached). catchsegv/nocache/eatmydata take no options.
    "time":        _WrapperGrammar(frozenset({"-a", "-p", "-q", "-v", "-V", "--append", "--portability", "--quiet", "--verbose", "--version", "--help"}), frozenset({"-o", "-f", "--output", "--format"}), frozenset(), 0),
    "exec":        _WrapperGrammar(frozenset({"-c", "-l"}), frozenset({"-a"}), frozenset(), 0),
    "command":     _WrapperGrammar(frozenset({"-p", "-V", "-v"}), frozenset(), frozenset(), 0),
    "chrt":        _WrapperGrammar(frozenset({"-f", "-b", "-o", "-r", "-i", "-d", "-R", "-m", "-a", "-v", "--fifo", "--batch", "--other", "--rr", "--idle", "--deadline", "--reset-on-fork", "--all-tasks", "--verbose"}), frozenset(), frozenset({"-p", "--pid"}), 1),
    "taskset":     _WrapperGrammar(frozenset({"-a", "--all-tasks", "-c", "--cpu-list"}), frozenset(), frozenset({"-p", "--pid"}), 1),
    "ionice":      _WrapperGrammar(frozenset({"-t", "--ignore"}), frozenset({"-c", "--class", "-n", "--classdata"}), frozenset({"-p", "--pid"}), 0),
    "unbuffer":    _WrapperGrammar(frozenset({"-p"}), frozenset(), frozenset(), 0),
    "proxychains": _WrapperGrammar(frozenset({"-q", "--quiet"}), frozenset({"-f", "--config"}), frozenset(), 0),
    "torsocks":    _WrapperGrammar(frozenset({"-h", "-q", "-i", "--isolate", "-H", "-V", "--version", "--help", "--shell"}), frozenset({"-d", "--debug", "-a", "--address", "-p", "--port", "-P", "--control-port", "-u", "--user", "-s", "--socks5"}), frozenset(), 0),
    "catchsegv":   _WrapperGrammar(frozenset(), frozenset(), frozenset(), 0),
    "nocache":     _WrapperGrammar(frozenset(), frozenset({"-n"}), frozenset(), 0),
    "eatmydata":   _WrapperGrammar(frozenset(), frozenset(), frozenset(), 0),
    "rlwrap":      _WrapperGrammar(frozenset({"-A", "-c", "-h", "-i", "-I", "-n", "-N", "-o", "-r", "-R", "-U", "-W", "-x", "-a", "-m", "-p", "--always-readline", "--complete-filenames", "--case-insensitive", "--no-children", "--one-shot", "--remember", "--quiet", "--help"}), frozenset({"-b", "-C", "-D", "-e", "-f", "-g", "-H", "-l", "-O", "-P", "-q", "-s", "-S", "-t", "-w", "-z", "--break-chars", "--command-name", "--history-no-dupes", "--forget-matching", "--file", "--history-filename", "--logfile", "--substitute-prompt", "--prompt", "--quote-characters", "--histsize", "--set-terminal-name", "--wait-before-prompt", "--filter"}), frozenset(), 0),
    # busybox + stdbuf (#1178 F2). busybox: the wrapper-usage form is `busybox APPLET [args]` with NO
    # own-flags before the applet — empty grammar, positionals=0, so the nested command is token[1]
    # (the applet); its exec-capable shell applets (sh/ash/hush/lash/msh) + awk are recognized
    # preserve-heads. stdbuf: the -i/-o/-e buffering-mode VALUE-flags (long --input/--output/--error);
    # the MODE value attaches (`-oL`), separates (`-o L`), or long-equals (`--output=L`) — the
    # attached-short form is consumed by _wrapper_nested_command's PHASE-1 short-value branch. no
    # shell forms, positionals=0. --help/--version are unlisted → unrecognized → preserve (over-block-safe).
    "busybox":     _WrapperGrammar(frozenset(), frozenset(), frozenset(), 0),
    "stdbuf":      _WrapperGrammar(frozenset(), frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}), frozenset(), 0),
}


def _stripped_surface_danger(stripped: str) -> bool:
    """The literal danger battery over an ALREADY-STRIPPED (or MASKED) surface: the
    DANGEROUS_PATTERNS scan + the four per-leg literal-arm families + the additive
    whole-surface normalized-flag condition + the PER-LEG op loop
    (_flag_condition_danger_op per leg, filtered to _PER_LEG_OPS, for any recognized op in
    a NON-first leg). Factored out of ``is_dangerous_command`` (behavior-identical)
    so the #1178 preserve-predicate can consult the SAME danger predicate on a masked leg
    WITHOUT re-entering ``_strip_non_executable_content`` (which would recurse — the predicate
    runs INSIDE that strip). SURFACE-AGNOSTIC: it neither strips nor masks, only matches, so a
    ``_mask_shell_quotes(leg)`` surface is safe (masking is idempotent for these regexes).
    The SSOT for "is this surface dangerous" — the read floor and the preserve predicate can
    never drift."""
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(stripped):
            return True
    # Literal arms matched PER-LEG over the SAME surface (identical provenance to the read
    # floor). Legs computed ONCE and shared by all four arm loops.
    legs = _slice_stripped_legs(stripped)
    for _leg in legs:
        if any(arm.search(_leg) for arm in _FORCE_PUSH_LITERAL_ARMS):
            return True
    for _leg in legs:
        if any(arm.search(_leg) for arm in _CLOSE_LITERAL_ARMS):
            return True
    for _leg in legs:
        if any(arm.search(_leg) for arm in _API_LITERAL_ARMS):
            return True
        if _graphql_merge_mutation(_leg) is not None or _update_branch_target(_leg) is not None:
            return True
    for _leg in legs:
        if any(arm.search(_leg) for arm in _BRANCH_DELETE_LITERAL_ARMS):
            return True
    if _flag_condition_danger_op(stripped) is not None:
        return True
    # PER-LEG arms: the whole-surface union call above is FIRST-LEG-anchored, so a recognized
    # op in a NON-first leg (`cd /repo && git push origin main`, `cd /repo && git push origin
    # --delete feature`) needs a per-leg call — the SAME union arm per leg, filtered by the
    # SAME shared _PER_LEG_OPS constant the detect loop uses (→ mint==read by construction).
    # leg[0] is harmlessly re-checked (the loop runs only after the whole-surface call
    # returned None).
    for _leg in legs:
        if _flag_condition_danger_op(_leg) in _PER_LEG_OPS:
            return True
    return False


def _leg_token_spans(leg: str) -> "list[tuple[int, int]]":
    """Quote-aware shell-token spans (start, end) into `leg`: a token is a maximal run with NO
    UNQUOTED whitespace (a quoted or backslash-escaped whitespace stays INSIDE the token).
    Mirrors the ``_mask_shell_quotes`` scanner exactly (backslash-escape outside quotes; `\\`
    honored inside "…" but not '…'), so an embedded quoted span (`FOO=a"b c"d` = ONE token) never
    SPLITS a token and a quoted head (`"timeout"`) stays token[0] — the two failure modes a
    masked-`\\S+` tokenization has (an internal-quote span erases to spaces and either splits the
    token or erases a quoted head, misaligning the walk). Callers reach here only on a balanced
    leg (``_is_preserve_leg`` preserves an unbalanced one up front), so quotes always close."""
    spans: list[tuple[int, int]] = []
    i, n = 0, len(leg)
    tok_start = -1
    while i < n:
        c = leg[i]
        if c.isspace():
            if tok_start >= 0:
                spans.append((tok_start, i))
                tok_start = -1
            i += 1
            continue
        if tok_start < 0:
            tok_start = i
        if c == "\\":
            i += 2
            continue
        if c == "'" or c == '"':
            q = c
            i += 1
            while i < n:
                if q == '"' and leg[i] == "\\":
                    i += 2
                    continue
                if leg[i] == q:
                    i += 1
                    break
                i += 1
            continue
        i += 1
    if tok_start >= 0:
        spans.append((tok_start, n))
    return spans


def _wrapper_nested_command(leg: str, head: str) -> "str | None":
    """For a RECURSE-set wrapper leg, return the ORIGINAL-string substring of the nested
    command (quotes intact) for ``_is_preserve_leg`` to recurse on, or None → FAIL-SAFE
    PRESERVE. Tokens + offsets come from ``_leg_token_spans`` (quote-aware: an adjacent-concat
    arg stays ONE token, a quoted head stays token[0]); flag recognition reads the RAW token
    text (a NAME= prefix / a dash-flag is before any quote), and the returned slice indexes the
    ORIGINAL leg so the nested command keeps its quotes. Returns None on ANY uncertainty: an
    unrecognized dash-flag (unknown arity → can't safely skip its value = the mis-skip-past-
    executor under-block), a shell-invocation flag (env -S / sudo -i,-s,-e / doas -s / …), a
    value-flag at end-of-tokens, or no nested command."""
    g = _WRAPPER_GRAMMAR[head]
    spans = _leg_token_spans(leg)    # spans[0] is the wrapper head (quoted or not)
    i, n = 1, len(spans)
    # PHASE 1 — the wrapper's OWN flags (which PRECEDE any positional / the command). Stopping
    # flag-consumption once a non-flag or `--` is seen is load-bearing: a wrapper flag that
    # COLLIDES with a nested-command flag (taskset's `-c`=--cpu-list vs `bash -c`) must NOT be
    # re-consumed AFTER the positional — else `taskset MASK bash -c "danger"`'s `-c` (bash's)
    # would be eaten as taskset's, mis-locating the command → under-block.
    while i < n:
        start, end = spans[i]
        tok = leg[start:end]
        if tok == "--":
            i += 1
            break                                # end-of-options; rest is positionals + command
        if tok.startswith("-") and tok != "-":
            flagname = tok.split("=", 1)[0]
            if flagname in g.shell_flags:
                return None                      # shell-invocation form → preserve
            if flagname in g.value_flags:
                i += 1 if "=" in tok else 2      # --flag=value one token; --flag value two
                continue
            if flagname in g.bool_flags:
                i += 1
                continue
            # ATTACHED-SHORT-VALUE getopt form (`-oL` = short value-flag `-o` + glued value `L`).
            # A SHORT flag only (`-X`, single dash + one char), whose 2-char prefix is a DECLARED
            # value_flag, with the value glued on (len > 2). The glued remainder is the value, so
            # the whole token is consumed (i += 1). GENERAL across RECURSE wrappers (getopt-universal:
            # `-Xvalue` always binds the value to -X), never bundled-boolean shorts (the prefix must be
            # a value_flag). Cannot swallow the nested command: a command never starts with `-` (the
            # PHASE-4 bare-dash guard), and PHASE-1 stops at the first non-flag, so an executor's own
            # `-c` (which follows the non-flag command head) is never reached here. MONOTONIC: it only
            # ADVANCES past a wrapper option, never synthesizes danger — so it can only CLOSE an
            # over-block (recurse to an inert nested command), never open an under-block.
            if len(tok) > 2 and tok[1] != "-" and tok[:2] in g.value_flags:
                i += 1
                continue
            return None                          # unrecognized dash-flag → preserve
        break                                    # first non-flag token → positionals / command
    # PHASE 2 — the wrapper's leading POSITIONALS (timeout DURATION, chrt PRIORITY, taskset MASK).
    for _ in range(g.positionals):
        if i >= n:
            return None                          # ran out (positional missing) → preserve
        i += 1
    # PHASE 3 — env NAME=VALUE assignments (env has positionals=0; they precede the command).
    if head == "env":
        while i < n and re.match(r"^[A-Za-z_]\w*=", leg[spans[i][0]:spans[i][1]]):
            i += 1
    # PHASE 4 — the nested command head.
    if i >= n:
        return None                              # no command → preserve
    cmd_start, cmd_end = spans[i]
    if leg[cmd_start:cmd_end].startswith("-"):
        return None                              # a bare dash-flag is never a real command → preserve
    return leg[cmd_start:]                        # nested command head (original, quotes intact)


def _leg_command_word(leg: str) -> "str | None":
    """Return the leg SUBSTRING starting at the TRUE command word — skipping any LEADING
    env-assignment (`NAME=value`, quoted OR bare: `LC_ALL=C`, `FOO="a b"`) and redirection
    (`>`, `2>`, `&>`, `2>/dev/null`, `2>&1`, `< in`…) tokens that PRECEDE the command. Returns
    None when the leg is ONLY assignments/redirects (no command). SECURITY (#1178 head-
    displacement): a leading assignment/redirect displaces tokens[0], so `_is_preserve_leg`
    must compute the head on the TRUE command — else `LC_ALL=C bash -c "danger"` reads head
    `LC_ALL=C`, no preserve clause fires, and the executor's danger arg is stripped = a
    destructive UNDER-block. Mirrors the env WRAPPER's own NAME=VALUE skip (grammar PHASE 3)
    at the leg top level for the BARE prefix form. Quote-aware via _leg_token_spans."""
    spans = _leg_token_spans(leg)
    k, n = 0, len(spans)
    while k < n:
        start, end = spans[k]
        tok = leg[start:end]
        # NAME=value / NAME+=value / NAME= (empty) assignment — the `\+?` covers the append
        # form (`PATH+=/x`); the whole token is skipped so an embedded '='/'$'/quote in the value
        # (`FOO=a=b`, `FOO=$HOME`, `FOO="a b"` → carrier-4-stripped to `FOO=STRIPPED`) stays inside
        # the skipped token. (carrier 4 already stripped a non-expanded quoted RHS to NAME=STRIPPED
        # BEFORE carrier 10, so quotes are not relied on surviving.)
        if re.match(r"^[A-Za-z_]\w*\+?=", tok):
            k += 1
            continue
        if re.match(r"^(?:[0-9]*(?:&?[<>]|<>)|&>>?)", tok):   # redirection operator
            k += 1
            # a BARE operator (`>`, `>>`, `2>`, `<`, `&>`, and SPACED-target `>&`/`<&`/`2>&`, `>|`)
            # consumes the NEXT token as its target; a self-contained form (`2>&1`/`3>&2`/`>&2`
            # fd-dup, `2>/dev/null`/`>file` attached target) does not. The `[<>]&` alt classifies a
            # spaced redirect-both (`>& file`) as BARE (architect finding — else the walk lands on
            # `file` and misses the real executor = under-block); `2>&1`/`>&2` stay self-contained
            # (a trailing fd digit fails the fullmatch).
            if re.fullmatch(r"[0-9]*(?:[<>]{1,2}|[<>]&|>\|)|&>>?", tok):
                k += 1
            continue
        return leg[start:]                               # the true command word
    return None                                          # only assignments/redirects → no command


# What the interpreter-program parsers can raise on malformed or unexpected text.
_PARSER_ERRORS = (AttributeError, TypeError, ValueError, IndexError, KeyError, RecursionError)


def _interpreter_guarded_commands(command: str, depth: int) -> list[str]:
    """Every guarded command the programs this command hands an interpreter (-c/-e and
    stdin-fed code) pass directly to an exec primitive, read from the command as written
    and, when it has line continuations, as the shell splices them too (`python3 \\`
    then `-c '…'` on the next line), and inside the -c string a POSIX shell leg runs
    (`bash -c "python3 -c '…'"`, `bash -c 'python3 - <<EOF … EOF'`). A parser defect must never raise: the pre-hook
    turns a raised exception into a deny, which would block a good-faith command. When
    the direct-call parser raises on one program, that program is judged by
    `_guarded_literals_beside_primitive` as well, joined to the guarded commands read
    before the defect, so a defect neither refuses outright nor allows outright."""
    spliced = _normalize_line_continuations(command)
    try:
        programs = _stdin_fed_code(command) + _code_arg_programs(command)
        if spliced != command:
            programs += [p for p in _stdin_fed_code(spliced) + _code_arg_programs(spliced) if p not in programs]
    except _PARSER_ERRORS:
        return []
    found: list[str] = []
    for head, code in programs:
        read: list[str] = []
        try:
            _guarded_commands_directly(head, code, depth, read)
        except _PARSER_ERRORS:
            read += _guarded_literals_beside_primitive(head, code, depth)   # with what was read before it
        found += read
    if depth < _MAX_WRAPPER_DEPTH:                       # programs a shell's -c string hands on
        try:
            for inner in _shell_c_programs(command):
                found += _interpreter_guarded_commands(inner, depth + 1)
        except _PARSER_ERRORS:
            pass
    return found


# A POSIX shell's name as a word anywhere in a command: the cheap test before a -c read.
_SHELL_WORD_RE = re.compile(r"(?<![\w.-])(?:sh|bash|zsh|dash|ash|ksh(?:93)?|rksh|mksh|hush|lash|msh)(?![\w.-])")


@functools.lru_cache(maxsize=256)
def _shell_c_programs(command: str) -> tuple:
    """The -c strings the POSIX shell legs of `command` run, as written. The legs are
    the stripped command's, so a heredoc the outer shell feeds as data is not read; a
    heredoc written inside the quoted -c string belongs to the inner shell, so that
    string is taken from the command as written rather than from the strip."""
    if not _SHELL_WORD_RE.search(command):
        return ()
    command = _normalize_line_continuations(command)
    stripped = _neutralize_inert_help_legs(_strip_non_executable_content(command))
    written: list[str] | None = None
    out = []
    for leg in _slice_stripped_legs(stripped):
        inner = _shell_c_argument(leg, strict=False)
        if inner is not None and "<<HEREDOC_STRIPPED" in inner:
            if written is None:
                written = [w for w in (_shell_c_argument(part, strict=False) for part in _slice_stripped_legs(command)) if w]
            prefix = inner.split("<<HEREDOC_STRIPPED")[0]
            inner = next((w for w in written if w.startswith(prefix + "<<")), inner)
        if inner is not None:
            out.append(inner)
    return tuple(out)


def _interpreter_code_runs_guarded(command: str, depth: int) -> bool:
    """True iff a program this command hands an interpreter runs a guarded command
    (see _interpreter_guarded_commands)."""
    return bool(_interpreter_guarded_commands(command, depth))


def normalize_command_text(command: str) -> str:
    """A command's text for exact comparison: continuations spliced as the guard
    splices them, whitespace runs collapsed, ends stripped."""
    return " ".join(_normalize_line_continuations(command).split())


def interpreter_command_context(command: str) -> dict | None:
    """The extract_command_context of the ONE guarded command the interpreter programs
    in `command` pass to an exec primitive, read the way is_dangerous_command reads them
    (-c/-e and stdin-fed programs, eval/exec of a literal, backtick spans, Tcl exec,
    statement forms). None when they pass none, when their distinct contexts number
    more than one, or when the one has no operation_type. Never raises."""
    try:
        contexts: dict = {}
        for text in _interpreter_guarded_commands(command, 0):
            context = extract_command_context(text)
            if context.get("operation_type") is None:
                return None
            contexts[repr(sorted(context.items()))] = context
        return next(iter(contexts.values())) if len(contexts) == 1 else None
    except _PARSER_ERRORS:
        return None


# The shells whose -c string is a shell command line (not eval/ssh/su, whose argument
# is not read this way). Short flags a shell takes before -c without a value.
_POSIX_SHELL_HEADS_RE = re.compile(r"^(?:sh|bash|zsh|dash|ash|ksh(?:93)?|rksh|mksh|hush|lash|msh)$")
_SHELL_SHORT_BOOL_FLAGS = frozenset("abefhiklmnprstuvxBCEHPT")
_SHELL_LONG_BOOL_FLAGS = frozenset({"--norc", "--noprofile", "--login", "--posix"})
_REDIRECT_OPERATOR_RE = re.compile(r"^(?:\d*|&)(?:>>?|<)&?$")
_REDIRECT_WITH_TARGET_RE = re.compile(r"^(?:\d*|&)(?:>>?|<)\S+$")


def shell_c_string(command: str) -> str | None:
    """The string a POSIX shell leg runs with -c, unquoted as the shell receives it:
    `bash -lc 'gh pr merge 5'` gives `gh pr merge 5`. `command` is one leg. Leading
    NAME=VALUE words and the modeled exec wrappers (env, timeout, nice, sudo, ...) are
    walked past; the shell's own flags before -c may be bundled shorts (`-eu`, `-lc`),
    `-o NAME`, or --norc/--noprofile/--login/--posix; redirects may follow the string.
    Words after the string are its positional parameters (`sh -c '… "$1"' _ 5`): each
    `$0`…`$9` / `${N}`, `$@`, `$*` and `$#` the string reads is replaced by its words,
    quoted as the inner shell would see them. None for anything else: another head, no
    -c, an unknown flag, an unbalanced quote, a parameter with no word or read as
    `${N…}`, or a string or word holding a command substitution the calling shell runs first
    (`$(…)` or a backtick outside single quotes). A `$VAR` is returned as written, as a
    bare command's text is approved. Never raises."""
    return _shell_c_argument(command, strict=True)


def _with_positional_words(text: str, words: list[str]) -> str | None:
    """`text`, a shell command line, with each `$N` / `${N}` it expands (outside single
    quotes) replaced by `words[N]`, `$@` / `$*` by `words[1:]` and `$#` by their count,
    quoted for where it sits; None when it reads a parameter with no word, `"$@"` with
    no words, or a `${N…}` expansion."""
    out, k, quote = [], 0, ""
    while k < len(text):
        ch = text[k]
        if quote != '"' and ch == "'":
            quote = "" if quote == "'" else "'"
        elif quote != "'" and ch == '"':
            quote = "" if quote == '"' else '"'
        elif quote != "'" and ch == "\\":
            out.append(text[k:k + 2])
            k += 2
            continue
        elif quote != "'" and ch == "$":
            m = re.compile(r"\$(?:(\d)|\{(\d)\})").match(text, k)
            if m:
                n = int(m.group(1) or m.group(2))
                if n >= len(words):
                    return None
                word = words[n]
                out.append(re.sub(r'([\\"$`])', r"\\\1", word) if quote == '"' else shlex.quote(word))
                k = m.end()
                continue
            if text.startswith(("$@", "$*"), k):
                rest = words[1:]
                if quote != '"':
                    out.append(" ".join(shlex.quote(w) for w in rest))
                elif text[k + 1] == "*":
                    out.append(re.sub(r'([\\"$`])', r"\\\1", " ".join(rest)))
                elif not rest:
                    return None
                else:                                    # "$@": one quoted word each
                    out.append('" "'.join(re.sub(r'([\\"$`])', r"\\\1", w) for w in rest))
                k += 2
                continue
            if text.startswith("$#", k):
                out.append(str(len(words) - 1))
                k += 2
                continue
            if text.startswith("${", k) and re.match(r"\d", text[k + 2:k + 3]):
                return None
        out.append(ch)
        k += 1
    return "".join(out)


def _shell_c_argument(command: str, strict: bool, _depth: int = 0) -> str | None:
    """shell_c_string's reader. With `strict` False it also returns a string the calling
    shell expands first, and one whose positional words cannot be read in (as written):
    the text is still the command line the shell runs, for judging rather than minting."""
    leg = _leg_command_word(command)
    tokens = _shell_tokenize(leg) if leg else None
    if not tokens or leg is None:
        return None
    head = os.path.basename(tokens[0])
    if head in _EXEC_WRAPPERS_RECURSE:
        nested = _wrapper_nested_command(leg, head) if _depth < _MAX_WRAPPER_DEPTH else None
        return _shell_c_argument(nested, strict, _depth + 1) if nested else None
    if not _POSIX_SHELL_HEADS_RE.match(head):
        return None
    i, has_c = 1, False
    while i < len(tokens) and not has_c:
        tok = tokens[i]
        i += 1
        if tok in _SHELL_LONG_BOOL_FLAGS:
            continue
        if tok in ("-o", "+o"):
            i += 1                                       # the option name
        elif len(tok) > 1 and tok[0] == "-" and set(tok[1:]) <= _SHELL_SHORT_BOOL_FLAGS | {"c"}:
            has_c = "c" in tok
        else:
            return None                                  # a script path or an unknown flag
    spans = _leg_token_spans(leg)
    if not has_c or i >= len(tokens) or len(spans) != len(tokens):
        return None
    j = i + 1
    while j < len(tokens) and not (_REDIRECT_OPERATOR_RE.match(tokens[j]) or _REDIRECT_WITH_TARGET_RE.match(tokens[j])):
        j += 1                                           # the string's positional words
    string = _with_positional_words(tokens[i], tokens[i + 1:j]) if j > i + 1 else tokens[i]
    if not strict:
        return tokens[i] if string is None else string
    if string is None or any(
        re.search(r"\$\(|`", re.sub(r"'[^']*'", "", leg[spans[k][0]:spans[k][1]])) for k in range(i, j)
    ):
        return None
    rest = tokens[j:]
    while rest:
        if _REDIRECT_OPERATOR_RE.match(rest[0]) and len(rest) > 1:
            rest = rest[2:]
        elif _REDIRECT_WITH_TARGET_RE.match(rest[0]):
            rest = rest[1:]
        else:
            return None
    return string


def _guarded_literals_beside_primitive(head: str, code: str, depth: int) -> list[str]:
    """The coarse reading of a program the direct-call parser could not read: when it
    names an exec primitive, its string literals, and its literal argv lists joined,
    that read as a guarded command. A program whose calls cannot be told apart is
    searched for primitive names."""
    if depth >= _DIRECT_CALL_MAX_DEPTH:
        return []
    try:
        names_primitive = _program_names_primitive(head, code)
    except _PARSER_ERRORS:
        names_primitive = bool(_INTERPRETER_EXEC_PRIMITIVE_RE.search(code))
    if not names_primitive:
        return []
    texts = [_literal_body(lit) for lit in _PREFIXED_LITERAL_RE.finditer(code)]
    try:
        for opener in re.finditer(r"[\[(]", code):
            lst = _literal_list_at(code, opener.start(), head)
            texts += [shlex.join(items) for items in (lst[0] if lst else []) if len(items) > 1]
    except _PARSER_ERRORS:
        pass                                             # the list reader failed too: literals only
    return [text for text in texts if _is_dangerous_at_depth(text, depth + 1)]


def _interpreter_names_primitive(leg: str, head: str, tokens: list) -> bool:
    """True iff an interpreter leg's program reaches a process. When the program given
    on the command line can be read, it is judged by its calls and statements outside
    string literals. Otherwise (a script, a module, an unusual launch form) the whole leg
    is searched for any primitive substring, which preserves (the over-block-safe side).
    `deno eval` is deno's one-liner SUBCOMMAND, not a call to eval."""
    try:
        program = _program_from_tokens(head, tokens)
        verdict = False if program is not None else None
    except _PARSER_ERRORS:
        verdict = None                                   # a parser defect: whole-leg search
    if verdict is not None:
        # A readable program's quoted values are inert: whether it RUNS a guarded command
        # is judged once, by the direct-call check over the whole command (calls and
        # statement forms given the command, any string-expression shape).
        return verdict
    text = _script_launch_surface(leg, tokens)
    if text is None:
        text = _blank_literal_backticks(leg)
    if head == "deno" and len(tokens) > 1 and tokens[1] == "eval":
        text = re.sub(r"(?<![\w./-])deno\s+eval(?=\s|$)", "deno", text, count=1)
    return bool(_INTERPRETER_EXEC_PRIMITIVE_RE.search(text))


# A script file an interpreter runs: a path with a directory part or a script extension.
_SCRIPT_PATH_RE = re.compile(
    r"/|\.(?:py|pyw|js|mjs|cjs|ts|mts|rb|pl|pm|php|lua|r|tcl|sh|bash)$", re.IGNORECASE
)
# An interpreter flag that gives the program on the command line, or reads it from stdin
# (a bundled short flag counts when any of its letters does).
_INLINE_PROGRAM_FLAG_RE = re.compile(r"^-$|^-[A-Za-z]*[cemErpx]|^--(?:eval|print|command|exec)\b")


def _script_launch_surface(leg: str, tokens: list) -> str | None:
    """For a script-file launch (`<interpreter> [flags] path/to/x.py ARGS…`), the leg up to
    the script path with each ARG blanked: the arguments are the script's argv data. A
    shell substitution in them (unquoted, or unescaped in double quotes) runs before the
    script starts, so it stays. None when the leg is not a script-file launch the guard can
    place (an inline-program flag, a non-flag word before a path, a quoted path)."""
    head_at = leg.find(tokens[0])
    if head_at < 0:
        return None
    pos = head_at + len(tokens[0])
    for tok in tokens[1:]:
        if tok.startswith("-"):
            if _INLINE_PROGRAM_FLAG_RE.match(tok):
                return None
            pos = leg.find(tok, pos)
            if pos < 0:
                return None
            pos += len(tok)
            continue
        if not _SCRIPT_PATH_RE.search(tok):
            return None
        at = leg.find(tok, pos)
        if at < 0:
            return None
        end = at + len(tok)
        return leg[:end] + _substitution_spans_only(leg[end:])
    return None


def _substitution_spans_only(text: str) -> str:
    """`text` blanked except the shell substitutions that run: unquoted, or unescaped in
    double quotes. Same length; an unterminated substitution keeps the rest (fail closed)."""
    view = _mask_quotes_keeping_substitutions(text)
    out = [" "] * len(view)
    i, n = 0, len(view)
    while i < n:
        c = view[i]
        if c == "\\":
            i += 2
            continue
        if c == "$" and i + 1 < n and view[i + 1] == "(":
            _, end = _extract_dollar_paren(view, i)
        elif c == "`":
            _, end = _extract_backtick(view, i)
        else:
            i += 1
            continue
        end = min(end, n)
        out[i:end] = view[i:end]
        i = max(end, i + 1)
    return "".join(out)


def _blank_literal_backticks(text: str) -> str:
    """`text` with each LITERAL backtick replaced by a space: one inside single quotes, or
    one escaped by an odd backslash run (inside double quotes or unquoted). Such a backtick
    runs nothing, so it is not an exec primitive. An unescaped backtick in double quotes or
    unquoted opens a command substitution and stays. Same length as the input; an
    unterminated quote leaves the rest as it is."""
    if "`" not in text:
        return text
    out = list(text)
    i, n, quote = 0, len(text), None
    while i < n:
        c = text[i]
        if quote == "'":
            if c == "'":
                quote = None
            elif c == "`":
                out[i] = " "
            i += 1
            continue
        if c == "\\":
            if i + 1 < n and text[i + 1] == "`":
                out[i + 1] = " "
            i += 2
            continue
        if c == '"':
            quote = None if quote == '"' else '"'
        elif c == "'" and quote is None:
            quote = "'"
        i += 1
    return "".join(out)


_PHP_HEAD_RE = re.compile(r"^php[0-9.]*(?:-cgi)?$")


def _is_php_exec_leg(leg: str, _depth: int = 0) -> bool:
    """True iff this leg runs php code that names an exec primitive, so a php `echo`
    statement in that code can print the output of a shell call. Walks the modeled
    wrappers like _is_preserve_leg, and FAILS SAFE to True when a coarse wrapper, an
    uncertain wrapper boundary or the depth limit hides the head while php is named
    in the leg (True keeps the echo argument for the preserve check to judge)."""
    if not _INTERPRETER_EXEC_PRIMITIVE_RE.search(leg):
        return False
    cmd_leg = _leg_command_word(leg)
    if cmd_leg is None:
        return False
    tokens = _shell_tokenize(cmd_leg)
    if not tokens:
        return False
    names_php = any(_PHP_HEAD_RE.match(os.path.basename(t)) for t in tokens)
    head = os.path.basename(tokens[0])
    if _PHP_HEAD_RE.match(head):
        return True
    if head in _EXEC_WRAPPERS_COARSE:
        return names_php
    if head in _EXEC_WRAPPERS_RECURSE:
        if _depth >= _MAX_WRAPPER_DEPTH:
            return names_php
        nested = _wrapper_nested_command(cmd_leg, head)
        if nested is None:
            return names_php
        return _is_php_exec_leg(nested, _depth + 1)
    return False


def _leg_slicing_view(text: str) -> str:
    """The view legs are sliced on: quotes masked and fd redirects (`2>&1`, `>&2`,
    `&>`, `>|`) blanked, so only real shell operators separate legs. Same length as
    `text`, so offsets carry over. Shared by carrier 10 and the echo carrier."""
    return _FD_REDIRECT_RE.sub(lambda m: " " * len(m.group()), _mask_shell_quotes(text))


def _in_php_exec_code(text: str, pos: int) -> bool:
    """True iff offset `pos` of `text` sits inside a quoted span of a leg that
    _is_php_exec_leg accepts, i.e. inside php code whose `echo` prints a shell call."""
    view = _leg_slicing_view(text)
    if view[pos] != " " or text[pos] == " ":
        return False                                     # not inside a quoted span
    start = 0
    for m in _COMPOUND_OPS_RE.finditer(view):
        if m.start() >= pos:
            return _is_php_exec_leg(text[start:m.start()])
        start = m.end()
    return _is_php_exec_leg(text[start:])


_QUOTED_ASSIGNMENT_RE = re.compile(r"""[A-Za-z_]\w*=["']""")


def _has_shell_quoted_assignment(leg: str) -> bool:
    """True iff the leg holds a shell `NAME="…"` / `NAME='…'` assignment: one whose
    `NAME=` sits in shell structure, not inside another quoted string. A perl
    `$c="…"` inside `perl -e '…'` is program text. An unterminated quote leaves the
    rest unmasked, so it still counts."""
    view = _mask_shell_quotes(leg)
    return any(view[m.start()] != " " for m in _QUOTED_ASSIGNMENT_RE.finditer(leg))


def _is_preserve_leg(leg: str, _depth: int = 0) -> bool:
    """True iff this single leg's quoted-arg values must be PRESERVED (not stripped) by the
    #1178 general inert-default strip — see the catalog block above for clauses (a)/(b)/(c).
    FAIL-SAFE to PRESERVE (True) on any ambiguity (unparseable quotes, uncertain wrapper
    boundary, recursion depth): preserving is the OVER-BLOCK-safe direction (it can only leave
    an over-block unclosed, never open an under-block)."""
    if _shell_tokenize(leg) is None:
        return True                                      # unbalanced quote → fail-safe preserve
    # (d1) a QUOTED variable-assignment RHS that survived the variable-assignment carrier (4)
    # is an EXPANDED value: carrier 4 strips a non-expanded `NAME="…"` to `NAME=STRIPPED`, and
    # when eval/source is present it skips entirely (preserving all) — either way, a `NAME="…"`
    # / `NAME='…'` still QUOTED here is one carrier 4 kept because it executes via its expansion
    # (`FOO="gh pr merge 5" && $FOO`, `export CMD="…" && eval $CMD`). Carrier 10 must NOT
    # re-strip it (under-block). Covers leading (`FOO=`) and builder (`export/declare NAME=`)
    # forms alike. NOTE: this un-anchored regex also matches a `--flag="v"` (the `flag="`
    # inside) — but carrier 4 pre-strips flag-attached literals before carrier 10, so it is a
    # no-op there; and either way the false-match direction is always PRESERVE (over-block-safe,
    # never under-block).
    if _has_shell_quoted_assignment(leg):
        return True
    # HEAD-DISPLACEMENT guard (#1178 SEC): compute the head clauses on the TRUE command word
    # (skip leading env-assignment / redirection prefixes) — else a leading `LC_ALL=C` / bare
    # `FOO=bar` / `2>/dev/null` displaces tokens[0] and the real executor is missed (under-block).
    cmd_leg = _leg_command_word(leg)
    if cmd_leg is not None:
        tokens = _shell_tokenize(cmd_leg)
        if tokens:
            head = os.path.basename(tokens[0])
            if head in _TOOL_HEADS:                      # (d2) git/gh → defer to carriers 5/7/8/9
                return True
            if _INTERPRETER_HEADS.match(head):           # (a') interpreter
                if _interpreter_names_primitive(leg, head, tokens):
                    return True
            elif _SHELL_STRING_EXECUTORS.match(head):    # (a) shell / string runner
                return True
            if head in _HTTP_CLIENT_HEADS:               # (b) curl/wget (quoted head via shlex)
                return True
            if head in _EXEC_WRAPPERS_COARSE:            # (a) wrapper, boundary not skippable
                return True
            if head in _EXEC_WRAPPERS_RECURSE:           # (a) wrapper, recurse to nested command
                if _depth >= _MAX_WRAPPER_DEPTH:
                    return True                          # depth guard → fail-safe preserve
                nested = _wrapper_nested_command(cmd_leg, head)
                if nested is None:
                    return True                          # uncertain boundary → fail-safe preserve
                return _is_preserve_leg(nested, _depth + 1)
    masked = _mask_shell_quotes(leg)                     # tail checks on the WHOLE leg (conservative)
    # (d3) an unquoted process substitution `<(cmd)` / `>(cmd)` EXECUTES cmd, so its quoted args
    # may execute — preserve. Masking blanks a quoted `"<("`, so only a REAL procsub triggers.
    if _PROCSUB_MARKER_RE.search(masked):
        return True
    if re.search(_GH_API_PREFIX, masked):                # (b) gh api (unquoted `gh api`)
        return True
    return _stripped_surface_danger(masked)              # (c) bare-token danger (defense-in-depth)


def _strip_quoted_positionals(leg: str) -> str:
    """Strip a leg's quoted positional values: dq span-scopes $()/backtick (via
    _strip_inert_dq_value); sq → bareword STRIPPED. Pure factor of the two inline
    substitutions the carrier-10 inert-default strip has always applied — shared with the
    read-verb carve-out's positional-strip verbs (git grep / gh search), where every
    quoted span surviving the carve-out's guards is data the verb never executes
    (patterns, pathspecs, query terms)."""
    stripped = re.sub(r'"(?:[^"\\]|\\.)*"', _strip_inert_dq_value, leg)
    return re.sub(r"'[^']*'", "STRIPPED", stripped)


# git global options that PRECEDE the subcommand (for _resolve_git_subcommand, #1195 OBS-A1).
# VALUE-taking (skip the flag AND its value token):
_GIT_GLOBAL_VALUE_FLAGS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env",
})
# Boolean (skip the flag token only):
_GIT_GLOBAL_BOOL_FLAGS = frozenset({
    "--no-pager", "--paginate", "-p", "--bare", "--no-replace-objects",
    "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
    "--icase-pathspecs", "--no-optional-locks",
})


def _resolve_git_subcommand(tokens: "list[str]") -> "int | None":
    """tokens[0] == 'git'; skip leading git GLOBAL flags to return the SUBCOMMAND token
    index, or None if none is reached. Skips ONLY dash-prefixed global flags (never a
    positional word), so a DESTRUCTIVE subcommand always resolves to ITSELF —
    `git push origin grep ':main'` -> 'push' (index 1, the first non-flag token), never
    'grep'. This is the load-bearing invariant for destructive-still-gates: the resolver
    can never gobble a positional into a read-verb key. An UNKNOWN global dash-flag returns
    None (fail-safe: don't resolve -> the leg preserves = over-block-safe)."""
    i, n = 1, len(tokens)
    while i < n:
        t = tokens[i]
        if t in _GIT_GLOBAL_VALUE_FLAGS:
            i += 2                                   # flag + its value token
            continue
        if "=" in t and t.split("=", 1)[0] in _GIT_GLOBAL_VALUE_FLAGS:
            i += 1                                   # --flag=value (one token)
            continue
        if t in _GIT_GLOBAL_BOOL_FLAGS:
            i += 1
            continue
        if t.startswith("-"):
            return None                              # unknown global flag -> fail-safe preserve
        return i                                     # first non-flag token = subcommand
    return None


def _strip_read_verb_values(leg: str, _depth: int = 0) -> "str | None":
    """READ-VERB value carve-out (pre-d2): strip the inert search/filter VALUES of a
    closed set of git/gh read verbs (_READ_VERB_SPECS) so a danger-looking quoted
    literal there (`git log --grep "…"`, `git grep '…'`, `gh pr list --search '…'`)
    no longer gates a faithful click via the d2 _TOOL_HEADS wholesale preserve.

    CONTRACT: returns None = "not a read-verb leg / guarded — fall through to the
    existing preserve/strip path"; returns a str = the final leg text (possibly
    identical). NEVER returns a quote-unbalanced string (guard g7).

    Runs INSIDE the per-leg walk: legs are sliced on the masked+FD-neutralized view
    BEFORE any strip fires, so a destructive sibling leg is not present in this
    function's input — separator-crossing (`git log --grep=foo&&gh pr merge 5`) is
    impossible BY CONSTRUCTION. A whole-string variant of this strip would reopen
    that under-block and is a forbidden alternative.

    MONOTONICITY (same argument as the carrier-10 strip): only listed flag VALUES
    and (for the positional-strip verbs) quoted spans are replaced with STRIPPED —
    it never synthesizes a danger token and never touches unquoted structure beyond
    a listed flag's own value, so it can only CLOSE an over-block (True→False),
    never open one; the safety burden reduces to guards g2/g3/g4/g6 keeping every
    load-bearing (executing) quoted value out of its reach. Each guard closes a
    live under-block vector:
      g2: with `eval` anywhere in the command, carrier 4 skips ALL assignments — a
          `FOO="gh pr merge 5 --admin"` surviving inside this leg is executed by a
          sibling `eval $FOO` leg; stripping positionals here would remove the
          only evidence.
      g3: `git grep $(sh -c '…')` — a bare $()/backtick span in the leg executes;
          whole-leg guard is deliberately coarser than the carriers' dq
          span-scoping (simplest sound bound; the cost is an unclosed over-block
          on `--grep 'x' $(date)`-shaped legs — status-quo residual).
      g4: `git grep -f <(sh -c '…')` — an unquoted process substitution executes.
      g6: an executing flag (git grep -O family) anywhere in the shlex tokens.
    Exact-token verb matching (tokens[1]/tokens[2]) is REQUIRED: a bounded
    word-gobbler prefix would let `git push origin grep ':main'` bind the grep
    spec and strip a deletable quoted refspec (an under-block). Global-flag
    spellings (`git -C x log …`) therefore fall through to d2 preserve —
    status-quo residual, tracked."""
    # ---- guards: ANY hit => return None (fall through to _is_preserve_leg) ----
    if _shell_tokenize(leg) is None:                 # g1 unbalanced quote (fail-safe)
        return None
    if _has_shell_quoted_assignment(leg):            # g2 d1-replica: a NAME="…" survivor
        return None                                  #    is an expanded/eval'd value
    if _has_command_substitution(leg):               # g3 $()/backtick anywhere in the leg
        return None
    cmd_leg = _leg_command_word(leg)                 # g5a env/redirect prefix skip
    if cmd_leg is None:
        return None
    tokens = _shell_tokenize(cmd_leg)
    if not tokens:
        return None
    head = os.path.basename(tokens[0])
    if head == "git":
        si = _resolve_git_subcommand(tokens)         # A1: skip git globals to the real verb
        if si is None:                               #    (git -C x log …, git -c k=v log …)
            return None
        key = ("git", tokens[si])
    elif head == "gh" and len(tokens) >= 3:
        key = ("gh", tokens[1], tokens[2])
    elif head in _EXEC_WRAPPERS_RECURSE and _depth < _MAX_WRAPPER_DEPTH:
        # A1: a read-only WRAPPER prefix (timeout/nice/env/…) — resolve the nested command
        # and RECURSE. _wrapper_nested_command returns preserve (None) for shell-invocation
        # forms (env -S, sudo -s) and any wrapper whose boundary is uncertain, so a wrapper
        # that CHANGES execution semantics never resolves to a strippable read verb (no
        # under-block). A stripped nested maps back under the verbatim wrapper prefix:
        # `nested` is a suffix of `leg`, so the prefix is byte-preserved by length.
        nested = _wrapper_nested_command(cmd_leg, head)
        if nested is None:
            return None
        rv = _strip_read_verb_values(nested, _depth + 1)
        if rv is None:
            return None
        mapped = leg[: len(leg) - len(nested)] + rv
        if _shell_tokenize(mapped) is None:          # g7 balance fail-safe on the mapped leg
            return leg
        return mapped
    else:
        return None
    spec = _READ_VERB_SPECS.get(key)                 # g5b exact-verb miss => None
    if spec is None:
        return None
    if _PROCSUB_MARKER_RE.search(_mask_shell_quotes(leg)):   # g4 procsub in leg (d3 replica)
        return None
    if spec.deny_re is not None and any(
        spec.deny_re.match(t) for t in tokens
    ):                                               # g6 executing-flag deny (shlex tokens —
        return None                                  #    a quoted '-O' is still the flag)
    # ---- strips (quote-balanced via the certified _strip_flag_values arms) ----
    out = leg
    if spec.flag_arm is not None:
        out = _strip_flag_values(out, spec.flag_arm.pattern, _keep_carrier_value)
    if spec.strip_positionals:
        out = _strip_quoted_positionals(out)
    if _shell_tokenize(out) is None:                 # g7 quote-balance fail-safe: NEVER
        return leg                                   #    emit a dangling quote (leg-merge)
    return out


def _maybe_strip_leg(leg: str) -> str:
    """Strip a leg's quoted positional VALUES iff it is inert (not preserved); else verbatim.
    The read-verb carve-out (_strip_read_verb_values) runs FIRST — before the d2 _TOOL_HEADS
    clause inside _is_preserve_leg can preserve a git/gh read-verb leg wholesale. dq
    span-scopes $()/backtick (via _strip_inert_dq_value); sq → bareword STRIPPED. The verb +
    unquoted structure stay intact; the leg carries NO shell-operator separator (those are
    outside the leg), so this never re-slices."""
    rv = _strip_read_verb_values(leg)
    if rv is not None:
        return rv                                    # already balance-checked inside
    if _is_preserve_leg(leg):
        return leg
    stripped = _strip_quoted_positionals(leg)
    # QUOTE-BALANCE fail-safe (#1118 leg-merge guard): the per-span dq regex is NOT
    # $()-nesting-aware, so on an exotic nested-quote value (`"a $(b "c") d"`) it can mis-pair
    # and emit an unbalanced leg. An unbalanced leg fed downstream could let _mask_shell_quotes
    # pair a stray quote ACROSS a separator and MERGE legs (under-block). shlex is the oracle:
    # we only reach here with a balanced ORIGINAL (an unbalanced one preserved at the top), so
    # if the strip broke balance, preserve the ORIGINAL leg rather than emit a dangling quote.
    if _shell_tokenize(stripped) is None:
        return leg
    return stripped


def _strip_inert_positional_legs(result: str) -> str:
    """Carrier-10 core (#1178): slice `result` into shell-operator legs — the SAME masked +
    FD-neutralize substrate as ``_slice_stripped_legs``, but RETAINING the original separators
    — and strip each INERT (non-preserved) leg's quoted positional values, emitting preserve
    legs verbatim. Rejoined with the ORIGINAL separators so a downstream re-slice of this
    result sees byte-unchanged leg boundaries. Slices via this in-line walk (NOT
    ``_split_into_legs``, which re-strips → recursion)."""
    view = _leg_slicing_view(result)
    out, last = [], 0
    for m in _COMPOUND_OPS_RE.finditer(view):
        out.append(_maybe_strip_leg(result[last:m.start()]))
        out.append(result[m.start():m.end()])            # ORIGINAL separator, verbatim
        last = m.end()
    out.append(_maybe_strip_leg(result[last:]))
    return "".join(out)


def _is_inert_help_leg(leg: str) -> bool:
    """True iff `leg` is an INERT help invocation that mutates nothing — a `--help`
    / `-h` help request, or a `gh|git help <sub>` head form. Such a leg contributes
    NO danger: `gh pr merge --help`, `git push --force --help`, `gh help pr merge`
    all print help and exit without merging/pushing, so gating them is pure friction
    (a cardinal over-block under the good-faith model).

    QUOTE-AWARE + VALUE-FLAG-AWARE (fail-TOWARD-gating on any ambiguity):
      - Tokens come from the quote-aware `_leg_token_spans`, so a `--help` living
        INSIDE a quoted value (`gh pr merge 5 --comment "…--help…"`) is one value
        token, never a bare flag — it stays gated.
      - A `-h` (or `--help`) that is the VALUE of a gh-pr value-taking flag is SKIPPED
        with its flag, so the decoy is not read as help — it stays gated. Both the
        shared LONG-form SSOT (`--subject -h`) AND the inert-recognizer supplement
        `_INERT_HELP_EXTRA_VALUE_FLAGS` — the short aliases `-t`/`-b`/`-F`/`-A`/`-c` plus
        `--repo`/`-R` (#1203 C1b) — are covered, across gh pr merge AND close.
      - Only an EXACT `--help` / `-h` flag token counts; a bundled short cluster
        (`-fh`) is NOT `-h`, so it does not match and the command stays gated
        (fail-toward-gating). An unbalanced-quote leg abstains (→ not inert → gated).

    The `gh|git help <sub>` head form is ADJACENCY-strict (#1203 finding-#4): it fires
    only when `help` is the IMMEDIATE raw token after the git/gh head (past NAME=value
    env-assignments), and a `-h`/`--help` flag counts only AT/AFTER the subcommand
    position (the first non-dash raw token after the head). A git GLOBAL value-option
    whose VALUE is literally `help`/`-h`/`--help` (`git -C help push --force`,
    `git -C -h push --force`) therefore stays GATED — its value sits in the leading
    global-option region, not the subcommand slot. Both checks read the FULL raw token
    stream, so an intervening dash-option is visible; a quoted `"gh help"` decoy in a
    value never triggers it. Enumeration-free (no git global-value-option list) and
    fail-safe toward gating."""
    if _shell_tokenize(leg) is None:
        return False                              # unbalanced quotes → abstain (gate)
    tokens = [leg[s:e] for s, e in _leg_token_spans(leg)]
    # RAW quote-stripped token stream (#1203 finding-#4): BOTH the head-form adjacency
    # AND the flag-loop subcommand-anchor are computed on the FULL stream — NEVER a
    # dash-filtered positional list — so a git GLOBAL value-option positioned between the
    # git/gh head and its target (`git -C help …`, `git -C -h …`) stays VISIBLE. The old
    # dash-filtered list dropped it, misreading the option's VALUE as the `help`
    # subcommand (head-form) or as a `-h` help flag (flag-loop) and UN-GATING a real
    # destructive leg — the two siblings of the value-decoy gap C1b closed. ENUMERATION-
    # FREE: neither arm needs to know which git global options take values; both key on
    # token ADJACENCY / POSITION, failing TOWARD gating (avoids the --match-head-commit
    # enumeration-drift trap).
    raw = [_strip_surrounding_quotes(t) for t in tokens]
    # Command head past leading NAME=value env-assignments (`FOO=bar git help push`
    # still resolves git as the head).
    head = 0
    while head < len(raw) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", raw[head]):
        head += 1
    is_git_gh_head = head < len(raw) and raw[head] in ("gh", "git")
    # ARM 1 — head-form ADJACENCY: `gh|git help <sub>` is inert ONLY when `help` is the
    # IMMEDIATE next raw token after the git/gh head. An intervening dash-token (a global
    # value-option like `-C`, whose value happens to be literally `help`) breaks the
    # adjacency → the head-form does NOT fire → the danger battery gates the real
    # destructive leg (`git -C help push --force` gates).
    if is_git_gh_head and head + 1 < len(raw) and raw[head + 1] == "help":
        return True
    # ARM 2 — flag-loop SUBCOMMAND-ANCHOR: a `-h`/`--help` is a help flag ONLY at/after
    # the SUBCOMMAND position — the FIRST NON-DASH raw token after the git/gh head. A
    # `-h`/`--help` in the LEADING global-option region (before that token) is a git
    # global value-option's VALUE (`git -C -h …`, `git --git-dir --help …`), NOT help. A
    # non-git/gh head has no global-option region to protect → anchor 0 (any help flag is
    # help). Structural — no git global-value-option enumeration.
    anchor = 0
    if is_git_gh_head:
        anchor = head + 1
        while anchor < len(raw) and raw[anchor].startswith("-") and raw[anchor] != "-":
            anchor += 1
    # Exact --help/-h flag token (at/after the ARM-2 anchor), skipping any gh-pr
    # value-taking flag's value token (#1203 C1b: --subject/--body/… + short aliases
    # -t/-b/-F/-A/-c + --repo/-R), so a value `-h`/`--help` is never misread as help.
    # The C1b enumerated skip (gh-pr SUBCOMMAND value flags) and the ARM-2 structural
    # anchor (git LEADING global value-options) are COMPLEMENTARY — different option
    # classes. `i += 2` skips the flag AND its value; `"=" not in tok` leaves the inline
    # `--flag=value` form as one already-safe token.
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        flagname = tok.split("=", 1)[0]
        is_value_flag = (
            flagname in _GH_PR_VALUE_TAKING_FLAGS
            or flagname in _INERT_HELP_EXTRA_VALUE_FLAGS
        ) and "=" not in tok
        if is_value_flag:
            i += 2
            continue
        if tok in ("--help", "-h") and i >= anchor:
            return True
        i += 1
    return False


def _neutralize_inert_help_legs(stripped: str) -> str:
    """Blank every INERT help leg (`_is_inert_help_leg`) in an already-stripped
    surface with an EQUAL-LENGTH run of spaces, leaving separators + non-inert legs
    byte-untouched. Applied by `is_dangerous_command` BEFORE the danger battery, so
    a leg whose only danger is a help-suppressed op stops gating while every REAL
    destructive leg keeps its exact bytes and still gates (per-leg, NOT a whole-
    command prefilter: `--help && gh pr merge 6` blanks leg 1 only → leg 2 gates).

    Equal-length blanking preserves ALL offsets, so the battery's whole-surface
    scans (`DANGEROUS_PATTERNS`, `_flag_condition_danger_op`) and its per-leg re-slice
    map 1:1 back to the original — the same offset-preserving discipline the
    `_FD_REDIRECT_RE` neutralize uses in `_slice_stripped_leg_spans`. Idempotent, and
    a strict no-op on a surface with no inert leg."""
    spans = _slice_stripped_leg_spans(stripped)
    result = list(stripped)
    for start, end in spans:
        if _is_inert_help_leg(stripped[start:end]):
            for i in range(start, end):
                result[i] = " "
    return "".join(result)


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
    # Pre-strip detection: an eval fed by a heredoc runs the heredoc's text, which the
    # heredoc strip below would hide. Judge it before the strip runs.
    verdict = _eval_heredoc_verdict(command, 0)
    if verdict is not None:
        return verdict
    return _is_dangerous_at_depth(command, 0)


# A variable assigned a literal value is judged where the shell expands it, as if the value
# of the last assignment before it were written there. Heads whose arguments the guard reads: data
# tools (they print, match or store an argument) and the shells, wrappers and eval words
# whose argument is a command the guard judges. Any other program's argument (a script
# file, an unknown binary) is unreadable, so a variable passed there keeps the value
# executing (the command stays refused, and a faithful approval mints it).
_VAR_DATA_TOOL_HEADS = frozenset({
    "echo", "printf", "grep", "egrep", "fgrep", "rg", "cat", "wc", "head", "tail", "test",
    "[", "[[", "tee", "diff", "cmp", "jq", "git", "gh", "true", ":",
})
_VAR_RUNNER_HEADS = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "eval", "source", ".", "ssh", "exec", "xargs",
    "timeout", "env", "sudo", "nohup", "nice", "watch", "command", "time", "builtin",
})
_VAR_ASSIGNMENT_RE = re.compile(
    r"(?<![\w$])(?P<name>[A-Za-z_]\w*)="
    r"(?P<value>'[^']*'|\"(?:[^\"\\]|\\.)*\"|(?:[^\s;&|<>()'\"`$\\]|\\.)+)"
)
_VAR_SITE_RE = re.compile(r"\$(?:\{(?P<braced>[A-Za-z_]\w*)(?P<op>[^}]*)\}|(?P<bare>[A-Za-z_]\w*))")
_VAR_BINDING_RE = r"(?<![\w-])(?:export|declare|typeset|local|readonly|read|mapfile|readarray|for|select|let|unset)(?![\w-])[^;&|\n]*?(?<![\w$])"


def _quote_states(command: str) -> list[int] | None:
    """Per character: 0 unquoted, 1 inside single quotes, 2 inside double quotes, 3 a
    character a backslash makes literal. None when a quote is left open or a double-quoted
    span holds a command substitution (its inner quoting is not modeled)."""
    states = [0] * len(command)
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c == "\\":
            if i + 1 < n:
                states[i + 1] = 3
            i += 2
            continue
        if c == "'":
            j = command.find("'", i + 1)
            if j == -1:
                return None
            for k in range(i, j + 1):
                states[k] = 1
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            while j < n and command[j] != '"':
                if command[j] == "\\" and j + 1 < n:
                    states[j] = 2
                    states[j + 1] = 3 if command[j + 1] in '$`"\\\n' else 2
                    j += 2
                    continue
                if command[j] == "`" or command.startswith("$(", j):
                    return None
                states[j] = 2
                j += 1
            if j >= n:
                return None
            states[i] = states[j] = 2
            i = j + 1
            continue
        i += 1
    return states


def _var_site_is_readable(cmd: str, offset: int, depth: int = 0) -> bool:
    """Whether an expansion at `offset` of the simple command `cmd` sits where the guard
    reads the result: the command word, an argument of a data tool or of a shell, wrapper
    or eval word, or an interpreter's own program. False for an argument of anything
    else, and for any position this cannot place."""
    word = _leg_command_word(cmd)
    if word is None:
        return False
    base = len(cmd) - len(word)
    spans = _leg_token_spans(word)
    k = next((k for k, (s, e) in enumerate(spans) if s <= offset - base < e), None)
    if k is None:
        return False
    if k == 0:
        return True
    tokens = _shell_tokenize(word)
    if not tokens:
        return False
    head = os.path.basename(tokens[0])
    if head in _VAR_DATA_TOOL_HEADS or _POSIX_SHELL_HEADS_RE.match(head):
        return True
    if head in _WRAPPER_GRAMMAR and depth < _MAX_WRAPPER_DEPTH:
        nested = _wrapper_nested_command(word, head)
        if nested is None:
            return False
        start = len(word) - len(nested)
        if offset - base < start:
            return True                      # the wrapper's own argument
        return _var_site_is_readable(nested, offset - base - start, depth + 1)
    if head in _VAR_RUNNER_HEADS:
        return True
    # A script launch's words are read as its literal twin's are (the script-argument rule
    # judges a whole guarded command there; other text is data).
    if _script_launch_args(tokens) is not None:
        return True
    if _INTERPRETER_HEADS.match(head):
        program = _program_from_tokens(head, tokens)
        return program is not None and k < len(tokens) and tokens[k] == program
    return False


def _assignment_value(raw: str) -> str:
    """An assignment's value after quote removal: a quoted value's inside, or an unquoted
    word with its backslash escapes removed."""
    if raw[0] in "'\"":
        return raw[1:-1]
    return re.sub(r"\\(.)", r"\1", raw)


def _is_plain_literal_assignment(command: str, view: str, m: re.Match) -> bool:
    """Whether an assignment stands as its own simple command at the top level (not in a
    group, subshell, loop or branch, not a command prefix) with a value that does not
    expand (a double-quoted `$`, backtick or backslash)."""
    before = view[:m.start()]
    if len(_COMPOUND_OPEN_RE.findall(before)) > len(_COMPOUND_CLOSE_RE.findall(before)):
        return False
    if re.split(r"[;&|\n]", before)[-1].strip():
        return False                          # an argument (`-f query=…`), not an assignment
    rest = command[m.end():].lstrip(" \t")
    if rest and not rest.startswith((";", "\n", "&&", "||")):
        return False                          # a prefix assignment, piped, or backgrounded
    raw = m.group("value")
    return not (raw[0] == '"' and any(ch in raw[1:-1] for ch in "$`\\"))


def _expand_literal_variables(command: str, kept: list[str] | None = None) -> str:
    """`command` with each literal variable's expansions replaced by the value of the last
    assignment before them, written literally, when every expansion sits where the guard
    reads it (_var_site_is_readable). A name is left as it is (every value keeps executing,
    today's rule) when it is bound another way (export, read, a loop), assigned inside a
    group, subshell or branch or as a command prefix, expanded before its first assignment
    or with a parameter operator, or given a value that expands, or when an expansion sits
    in a heredoc's body; and nothing is replaced when the command has an
    eval or source, or quoting this cannot model. The value is inserted as literal text,
    escaped for the site's quoting. Given a `kept` list, each value left executing at a
    site the guard cannot read is appended to it."""
    if "=" not in command or "$" not in command:
        return command
    states = _quote_states(command)
    if states is None:
        return command
    # A heredoc's body is the heredoc's text, not the shell's: the surface is read with each
    # body blanked in place (offsets match the command), so an expansion there is left.
    bodies = [(h.start("body"), h.end("body")) for h in _ANY_HEREDOC_RE.finditer(command)]
    if bodies:
        blanked = list(command)
        for a, b in bodies:
            blanked[a:b] = " " * (b - a)
        surface = _mask_shell_quotes("".join(blanked))
    else:
        surface = _executed_surface_view(command)
    if _has_eval_or_source(surface):
        return command
    view = _leg_slicing_view(command)
    legs = []
    last = 0
    for sep in _COMPOUND_OPS_RE.finditer(view):
        legs.append((last, sep.start()))
        last = sep.end()
    legs.append((last, len(command)))
    replacements: list[tuple[int, int, str]] = []
    assignments: dict[str, list[re.Match]] = {}
    for m in _VAR_ASSIGNMENT_RE.finditer(command):
        if states[m.start()] == 0 and surface[m.start()] != " ":
            assignments.setdefault(m.group("name"), []).append(m)
    for name, found in assignments.items():
        if len(re.findall(r"(?<![\w$])" + name + r"=", surface)) != len(found):
            continue
        if re.search(_VAR_BINDING_RE + name + r"(?![\w])", surface):
            continue
        if not all(_is_plain_literal_assignment(command, view, m) for m in found):
            continue
        sites = []
        ok = True
        for s in _VAR_SITE_RE.finditer(command):
            if (s.group("braced") or s.group("bare")) != name:
                continue
            if states[s.start()] in (1, 3):
                continue                      # single-quoted or escaped: literal text
            if s.group("braced") and s.group("op"):
                ok = False
                break
            reaching = [m for m in found if m.end() <= s.start()]
            if not reaching or (states[s.start()] == 0 and surface[s.start()] == " "):
                ok = False
                break
            leg = next((a, b) for a, b in legs if a <= s.start() < b)
            leg_text = command[leg[0]:leg[1]]
            leg_view = view[leg[0]:leg[1]]
            if "$(" in leg_view or "`" in leg_view:
                ok = False
                break
            if not _var_site_is_readable(leg_text, s.start() - leg[0]):
                if kept is not None:
                    kept.append(_assignment_value(reaching[-1].group("value")))
                ok = False
                break
            sites.append((s, _assignment_value(reaching[-1].group("value"))))
        if not ok:
            continue
        # Each value now sits, literally, at every site the shell expands it, so the
        # assignments themselves run nothing: write them as the inert value carrier 4 gives
        # a variable that is never expanded.
        for m in found:
            replacements.append((m.start("value"), m.end("value"), "STRIPPED"))
        for s, value in sites:
            special = '\\$`"' if states[s.start()] == 2 else "\\$`'\""
            literal = "".join("\\" + ch if ch in special else ch for ch in value)
            replacements.append((s.start(), s.end(), literal))
    if not replacements:
        return command
    out = command
    for start, end, literal in sorted(replacements, reverse=True):
        out = out[:start] + literal + out[end:]
    return out


_XARGS_HEAD_RE = re.compile(r"(?:^|(?<=[;&|(\n]))[ \t]*xargs(?![\w-])", re.MULTILINE)
_XARGS_HEREDOC_RE = re.compile(
    r"<<-?[ \t]*(?P<q>['\"]?)(?P<tag>\w+)(?P=q)[^\n]*\n(?P<body>.*?)\n?[ \t]*(?P=tag)[ \t]*(?:\n|$)",
    re.DOTALL,
)
_XARGS_POSITIONAL_SCRIPT = ("$@", "${@}", "$1", "${1}", "$*", "${*}")
_XARGS_MAX_DEPTH = 3


def _xargs_shell_stdin_items(command: str) -> list[str]:
    """The input items an `xargs` leg hands a shell to run, when its stdin is a here-string
    or a heredoc: `xargs -I{} sh -c "{}" <<< "CMD"` runs CMD, as its pipe form
    `echo CMD | xargs -I{} sh -c "{}"` does. The leg's command must be a POSIX shell with
    -c whose script holds the replacement token (-I/-i, default `{}`) or a positional
    (`"$@"`, `"$1"`). Items are the input's lines, or its NUL- or -d-separated parts.
    An xargs flag the grammar does not know makes the leg abstain (no items)."""
    if "xargs" not in command or "<<" not in command:
        return []
    view = _mask_shell_quotes(command)
    grammar = _WRAPPER_GRAMMAR["xargs"]
    items: list[str] = []
    for head in _XARGS_HEAD_RE.finditer(view):
        end = head.end()
        while end < len(view) and view[end] not in ";&|\n":
            end += 1
        here = view.find("<<<", head.end(), end)
        if here != -1:
            feed_tokens = _shell_tokenize(command[here + 3:end])
            if not feed_tokens:
                continue
            feed = feed_tokens[0]
            argv_end = here
        else:
            doc = _XARGS_HEREDOC_RE.search(command, head.end())
            if doc is None or doc.start() >= end:
                continue
            feed = doc.group("body")
            argv_end = doc.start()
        argv = _shell_tokenize(command[head.end():argv_end])
        if not argv:
            continue
        token, delim, i = None, "\n", 0
        while i < len(argv) and argv[i].startswith("-"):
            flag = argv[i]
            if flag == "--":
                i += 1
                break
            if flag in ("-0", "--null"):
                delim = "\0"
            elif flag.startswith("-I"):
                token = flag[2:] or (argv[i + 1] if i + 1 < len(argv) else None)
                i += 0 if flag[2:] else 1
            elif flag.startswith(("-i", "--replace")):
                token = flag.partition("=")[2] if flag.startswith("--") else flag[2:]
                token = token or "{}"
            elif flag in ("-d", "--delimiter") or flag.startswith(("-d", "--delimiter=")):
                value = (argv[i + 1] if i + 1 < len(argv) else "") if flag in ("-d", "--delimiter") \
                    else flag.partition("=")[2] if flag.startswith("--") else flag[2:]
                delim = {"\\n": "\n", "\\0": "\0", "\\t": "\t"}.get(value, value) or "\n"
                i += 1 if flag in ("-d", "--delimiter") else 0
            elif flag in grammar.value_flags:
                i += 1
            elif flag[:2] in grammar.value_flags or flag.partition("=")[0] in grammar.value_flags:
                pass
            elif flag not in grammar.bool_flags:
                break
            i += 1
        if i >= len(argv) or argv[i].startswith("-"):
            continue
        shell = argv[i]
        if not _POSIX_SHELL_HEADS_RE.match(shell.rsplit("/", 1)[-1]):
            continue
        script = None
        for j in range(i + 1, len(argv)):
            flag = argv[j]
            if flag.startswith("-") and not flag.startswith("--") and "c" in flag[1:]:
                script = argv[j + 1] if j + 1 < len(argv) else None
                break
            if flag in _SHELL_LONG_BOOL_FLAGS or (flag.startswith("-") and set(flag[1:]) <= _SHELL_SHORT_BOOL_FLAGS):
                continue
            break
        if not script:
            continue
        if not ((token and token in script) or any(p in script for p in _XARGS_POSITIONAL_SCRIPT)):
            continue
        items += [part.strip() for part in feed.split(delim) if part.strip()]
    return items


def _is_dangerous_at_depth(command: str, depth: int) -> bool:
    """is_dangerous_command's body, with the recursion depth of the stdin-fed code check."""
    if depth:
        verdict = _eval_heredoc_verdict(command, depth)
        if verdict is not None:
            return verdict
    # A gh api graphql request whose --input body the guard can read is judged by its query.
    if _graphql_input_mutation(command) is not None:
        return True
    # A script given a whole guarded command as one argument runs it (a retry or run
    # wrapper), as a variable passed there does.
    if depth < _MAX_WRAPPER_DEPTH and _script_argv_guarded_command(command, depth) is not None:
        return True
    # A variable assigned a literal value is judged where it is expanded, as the value of
    # the last assignment before it written there; a site the guard cannot read keeps the
    # value executing. A script given the expanded value is judged as given the literal,
    # before a file run's rewrite drops the script's arguments.
    expanded = _expand_literal_variables(command)
    if (expanded != command and depth < _MAX_WRAPPER_DEPTH
            and _script_argv_guarded_command(expanded, depth) is not None):
        return True
    # A file written with literal text and then run is judged as the text it runs.
    if depth < _MAX_WRAPPER_DEPTH:
        rewritten = _rewrite_file_runs(command)
        if rewritten != command:
            return _is_dangerous_at_depth(rewritten, depth + 1)
    command = expanded
    # Code an interpreter reads from stdin (heredoc, here-string, pipe) is that
    # interpreter's program: it is refused when it passes a guarded command directly
    # to an exec primitive. The carriers below see it only as inert text.
    if _interpreter_code_runs_guarded(command, depth):
        return True
    # An xargs leg that runs a shell on each item of a here-string or heredoc runs those
    # items as commands, the same as its `echo … | xargs sh -c` pipe form.
    if depth < _XARGS_MAX_DEPTH and any(
        _is_dangerous_at_depth(item, depth + 1) for item in _xargs_shell_stdin_items(command)
    ):
        return True

    # Normalize bash line continuations (\<newline>) via the shared P0 SSOT before
    # any matching (so this floor + the substrate join lines identically).
    command = _normalize_line_continuations(command)
    stripped = _strip_non_executable_content(command)
    # C1 (#1203) — inert-help suppression: blank any leg that is an inert `--help`/
    # `-h` / `gh help` invocation BEFORE the danger battery, so a faithful help
    # request stops gating (a cardinal over-block). Per-leg + offset-preserving, so
    # every REAL destructive leg keeps its bytes and still gates. This is the SINGLE
    # load-bearing layer: the gate (check_merge_authorization), the compound counter
    # (is_compound_destructive_command → _leg_is_destructive), and the read/mint leg
    # selection (_single_destructive_leg) ALL route through is_dangerous_command, so
    # suppressing here makes every consumer agree the inert leg is non-gating.
    stripped = _neutralize_inert_help_legs(stripped)
    # A POSIX shell's -c string is a command line: judge it as the bare command is.
    if depth < _MAX_WRAPPER_DEPTH:
        for inner in _shell_c_programs(command):
            if _is_dangerous_at_depth(inner, depth + 1):
                return True
    # The literal danger battery (DANGEROUS_PATTERNS + the four per-leg literal-arm families +
    # the additive normalized-flag condition) is the SSOT _stripped_surface_danger — factored
    # out so the #1178 general-strip preserve-predicate can consult the SAME predicate on a
    # masked leg without re-entering the strip (recursion). Behavior-identical to the prior
    # inline battery.
    return _stripped_surface_danger(stripped)


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
    return [stripped[s:e] for s, e in _slice_stripped_leg_spans(stripped)]


def _slice_stripped_leg_spans(stripped: str) -> "list[tuple[int, int]]":
    """The (start, end) offset spans of each leg within `stripped` — the span form
    of `_slice_stripped_legs` (which is `[stripped[s:e] for s, e in ...]`, byte-
    identical output by construction). Factored out so an OFFSET-preserving consumer
    (the C1 inert-help neutralizer) can blank a leg IN PLACE with an equal-length
    run without re-deriving the leg boundaries — one leg-boundary substrate, no
    drift. Same masked + FD-neutralized view + `_COMPOUND_OPS_RE` scan as the slice
    form; the view exists ONLY to locate operator offsets, spans index `stripped`."""
    view = _FD_REDIRECT_RE.sub(
        lambda m: " " * len(m.group()), _mask_shell_quotes(stripped)
    )
    spans, last = [], 0
    for m in _COMPOUND_OPS_RE.finditer(view):
        spans.append((last, m.start()))
        last = m.end()
    spans.append((last, len(stripped)))
    return spans


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
    # An eval's heredoc lines are the command it runs, not legs of their own.
    command = _rewrite_readable_evals(command)
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
    one-op-at-a-time approval). Each distinct guarded command an interpreter program
    runs (`python3 -c '…run(["gh", "pr", "merge", "5"])'`) counts as one destructive
    leg, so `A && gh pr merge 6` with A such a program is compound.
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
    # A program handed to an interpreter is stripped from the legs above, so the
    # guarded commands such programs run are counted from the programs: interpreter
    # legs together count as the larger of their destructive legs and the distinct
    # commands their programs run, so neither reading counts one command twice.
    # When an eval runs a heredoc, the heredoc bodies stay in the legs, so the commands
    # the eval runs are counted from their own legs; the eval's leg is not counted again
    # for the eval+heredoc shape alone.
    if _has_eval_with_heredoc(command):
        destructive = [
            leg for leg in legs if _is_dangerous_at_depth(leg, 0) or _RM_HEAD_RE.match(leg)
        ]
    else:
        destructive = [leg for leg in legs if _leg_is_destructive(leg)]
    on_interpreters = sum(1 for leg in destructive if _INTERPRETER_WORD_RE.search(leg))
    interpreted = {normalize_command_text(c) for c in _interpreter_guarded_commands(command, 0)}
    in_shells = sum(_shell_leg_destructive_count(leg) for leg in destructive)
    in_shells += _literal_loop_extra_passes(legs)
    return in_shells - on_interpreters + max(on_interpreters, len(interpreted)) >= 2


_FOR_IN_LEG_RE = re.compile(r"\s*for\s+[A-Za-z_]\w*\s+in\s+(.*\S)\s*$", re.DOTALL)
_LOOP_HEAD_LEG_RE = re.compile(r"\s*(?:for|while|until|select)\s")
_DONE_LEG_RE = re.compile(r"\s*done(?![\w-])")
_LEADING_KEYWORDS_RE = re.compile(r"^\s*(?:(?:do|then|else)\s+|\{\s+)*")


def _literal_loop_extra_passes(legs: list[str]) -> int:
    """The destructive legs a `for NAME in W1 W2 …; do …; done` loop over literal words
    runs past its first pass: `for p in 5 6; do gh pr merge "$p"; done` runs two merges,
    so it adds 1 to the one destructive leg its body holds. A list that is not plain
    literal words (`$VAR`, `$(…)`, a glob or brace) adds nothing."""
    extra = 0
    legs = [_LEADING_KEYWORDS_RE.sub("", leg) for leg in legs]
    for i, leg in enumerate(legs):
        m = _FOR_IN_LEG_RE.match(leg)
        words = _shell_tokenize(m.group(1)) if m and not re.search(r"[$`*?\[{]", m.group(1)) else None
        if not words:
            continue
        depth, body = 0, 0
        for part in legs[i + 1:]:
            if _DONE_LEG_RE.match(part):
                if depth == 0:
                    break
                depth -= 1
            elif _LOOP_HEAD_LEG_RE.match(part):
                depth += 1
            if _leg_is_destructive(part):
                body += 1
        extra += (len(words) - 1) * body
    return extra


def _shell_leg_destructive_count(leg: str, depth: int = 0) -> int:
    """1 for a destructive leg, or for a POSIX shell leg the destructive legs its -c
    string chains, as the bare command counts them: `bash -c 'gh pr merge 5 && git
    branch -D x'` counts 2, and a string whose only guarded text is a comment counts 0."""
    inner = _shell_c_argument(leg, strict=False) if depth < _MAX_WRAPPER_DEPTH else None
    if inner is None:
        return 1
    inner_legs = _split_into_legs(inner)
    inner_destructive = [part for part in inner_legs if _leg_is_destructive(part)]
    return sum(
        _shell_leg_destructive_count(part, depth + 1) for part in inner_destructive
    ) + _literal_loop_extra_passes(inner_legs)
