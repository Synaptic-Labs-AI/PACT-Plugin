"""
Location: pact-plugin/tests/test_merge_guard_overblock_cluster_monotonicity.py
Summary: TEST-phase CORPUS-WIDE monotonicity sweep + intended-closure accounting for the
         merge-guard over-block cluster (#1181 / #1155 / #1148), the matrix-broadening
         closure rows (gh search commits/repos positionals + per-subcommand value-flags
         + git show/shortlog/log breadth — the coder's open question 2 and the auditor
         spot-set carry-forward, lead-approved), and the #1155 e2e-oracle faithfulness
         pin.

         This is the TEST-phase deliverable ABOVE the coder's per-commit cert rows (the
         lead-confirmed dividing line). The per-commit certs certify each fix in
         isolation; THIS file certifies that the COMPOSED base-vs-HEAD transition set
         over a broad corpus is EXACTLY {the union of the three certs' intended
         closures}, with ZERO base-False -> HEAD-True. That False->True gate is the
         cardinal-sin direction (a faithful click newly blocked); the "every True->False
         is intended" gate is the sole structural defense against an UNDER-block
         masquerading as a closure — both are True->False transitions, so only an
         enumerated allow-set can tell a real closure from a freed destructive op.

         METHOD: every row asserts against the REAL is_dangerous_command, BASE (committed
         vendored fixture via merge_guard_baseline_loader — loud-fail, CI-executable,
         never skip) vs HEAD (live worktree module). NEVER a byte-diff / additive-lines
         argument (the #1118 doctrine for this SACROSANCT control).

         R1 REMEDIATION EXTENSION (commits 4 & 5 — the PERMANENT gap-closure). An
         independent adversarial audit found two under-blocks this file's ORIGINAL
         curated corpus missed (exactly its own documented limitation: a spelling absent
         from the corpus can go unseen). Both classes are now folded into the standing
         corpus so a future regression is caught HERE, not only in the per-fix certs:
           * F1 escaped-predecessor (#1148, commit 4): a `#` after a backslash-escaped
             delimiter is NOT a bash comment; the escape-aware excision leaves the routing
             token visible -> stays GATED. is_dangerous-visible (base-True -> HEAD-True);
             _R1_ESC rides the DESTRUCTIVE battery; _R1_GENUINE (real comments still
             excised, base-True -> HEAD-False) rides the closure set as the over-block-
             safety companion. See TestR1EscapedPredecessor.
           * F2 quoted-remote/quoted-flag launder (#1155, commit 5): is_dangerous stays
             True on BOTH trees — the launder is an OP-DOWNGRADE (remote-ref-delete /
             remote-mass-delete / force-push -> push-to-main) that the is_dangerous sweep
             STRUCTURALLY CANNOT SEE. The catch is a DETECT-OP PARITY dimension
             (base.detect == HEAD.detect == restored op), see TestR1DetectOpParity; a thin
             standing e2e launder smoke rides in TestR1LaunderE2ESmoke.
           * F3 line-continuation splice (#1148, commit 6): a `#` after a backslash-newline
             is NOT a bash comment (bash SPLICES the line-continuation, gluing `#` to the
             preceding token). _F3_CLOSE (imported) rides the DESTRUCTIVE battery (base-True
             -> HEAD-True retention); _F3_GENUINE (imported) rides the closure set. F3 also
             introduces the SOLE legitimate base-False -> HEAD-True: the split-word bonus (a
             mid-word line-continuation that splices to a REAL force-push base under-blocked)
             -> EXPECTED_BONUS, a lead-ratified SINGLETON whose membership predicate is
             SEMANTIC (bash runs it as a destructive op, so gating it is never an over-block).
             The cardinal-direction gate is {base-False -> HEAD-True} SUBSET-OF EXPECTED_BONUS
             (was == EMPTY); any OTHER False->True still reds. See TestR3* classes.

         NON-VACUITY EVIDENCE (authoring-time measurements — documented here, NOT
         committed as git-show-by-SHA test code, per the #1182 CI-invisible-non-ancestor
         lesson):
           * per-commit DELETE-THE-FIX counter-tests (git-show parent-tree isolation at
             test-authoring time): every closure/gated row is at its PRE-flip value against
             its commit's PARENT tree and at its fixed value at the commit, so each fix is
             individually load-bearing —
               #1181 (796a83e3, parent b4041ccf): 20/20 closures pre-flip True->False
               #1155 (bf95a1f6, parent 796a83e3): 2/2 find closures pre-flip True->False
               #1148 (7b34e8cf, parent bf95a1f6): 2/2 comment closures pre-flip True->False
               F1   (7ca8de80, parent 4e130f6f): 10/10 _R1_ESC rows under-blocked
                     (is_dangerous False) at the parent, gated at HEAD
               F2   (f6f94c3e, parent 7ca8de80): 10/10 _R1_DELETE rows op-downgrade to
                     push-to-main at the parent, restored op at HEAD (is_dangerous True both)
               F3   (b4ea5bfc, parent 07a2994f): 11/11 _F3_CLOSE rows under-blocked
                     (is_dangerous False) at the parent, gated at HEAD; the split-word bonus
                     gate appears at commit 6 (parent False -> HEAD True force-push)
             The committed non-vacuity artifact is the base leg of every assertion below
             (baseline fixture) + the loader's pre-fix discriminators.
           * tree-wide harvest: 1620 command literals AST-harvested from the entire
             test_merge_guard*.py corpus and swept base-vs-HEAD -> 0 base-False->HEAD-True.

         Destructive verbs are assembled at runtime so this file carries no raw
         force-delete / force-push / merge literal and stays inert to the live guard.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

import shared.merge_guard_common as mgc  # noqa: E402
import merge_guard_pre  # noqa: E402  # R1/F2 e2e launder smoke
import merge_guard_post  # noqa: E402  # R1/F2 e2e launder smoke
from merge_guard_baseline_loader import load_baseline  # noqa: E402

# The three per-commit certs own the authoritative closure enumerations; import them so
# the allow-set below is COUPLED to the certs and cannot silently drift.
from test_merge_guard_1181_cert import INTENDED_CLOSURES as _C1181  # noqa: E402
from test_merge_guard_1155_cert import FIND_CLOSURES as _C1155  # noqa: E402
from test_merge_guard_1148_cert import CLOSURES as _C1148  # noqa: E402

# R1 remediation sets (commits 4 & 5) — imported for the SAME anti-drift coupling as the
# closure sets above; the per-fix cert modules own these enumerations.
#   _R1_ESC     (#1148 F1): escaped-# spellings that stay GATED (is_dangerous True->True).
#   _R1_GENUINE (#1148 F1): real comments that stay excised (base-True->HEAD-False closure;
#               the over-block-safety companion proving F1 did not over-reach).
#   _R1_DELETE  (#1155 F2): quoted-remote/quoted-flag launder rows — is_dangerous True BOTH
#   _R1_FAITHFUL(#1155 F2): faithful push rows. F2's bug is an OP-DOWNGRADE invisible to the
#               is_dangerous sweep, so these are guarded by TestR1DetectOpParity (detect op),
#               not the is_dangerous accounting.
from test_merge_guard_1148_cert import _ESC as _R1_ESC, _GENUINE as _R1_GENUINE  # noqa: E402
from test_merge_guard_1155_cert import (  # noqa: E402
    _R1_DELETE_CLASS as _R1_DELETE,
    _R1_FAITHFUL as _R1_FAITHFUL_ROWS,
)

# Combined (label, cmd, want_op) rows for the F2 detect-op parity guard.
_R1_OP_ROWS = _R1_DELETE + _R1_FAITHFUL_ROWS

# F3 line-continuation-splice sets (commit 6), imported for the SAME anti-drift coupling:
#   _F3_CLOSE   : line-continuation-before-# forms that GATE (base-True->HEAD-True retention)
#   _F3_GENUINE : genuine comments (bare-newline/plain-space/even-backslash/CRLF) that stay
#                 excised -> base-True->HEAD-False closures (the over-block-safety companion)
#   _F3_LC      : the backslash-newline literal, to build the flag-scan control + the bonus.
from test_merge_guard_1148_cert import (  # noqa: E402
    _F3_CLOSE,
    _F3_GENUINE,
    _LC as _F3_LC,
)

# OBS-A3/7e committer-census closure set (OBS commit 1): the carrier-7e -m-cluster
# `(?<!\S)` token-start anchor stops the mid-flag mis-match inside `--committer`, so the
# read-verb strip then cleans the INERT committer FILTER value -> `git log/show/shortlog
# --committer '<danger>'` flips base-True -> HEAD-False. git MATCHES a committer value, it
# never shell-executes it, so these were pre-existing OVER-BLOCKS (a benign committer value
# is never gated) — not destructive ops. Imported for anti-drift, same discipline as _R1/_F3.
from test_merge_guard_obs_cert import COMMITTER_CENSUS  # noqa: E402

D = mgc.is_dangerous_command


def _base():
    return load_baseline().is_dangerous_command


# --- Destructive literals assembled at runtime (inert to the live guard) ---
M5 = "gh " + "pr " + "merge 5 --admin"
MW = "mer" + "ge"
CD5 = "gh " + "pr " + "close 5 --delete-branch"
PF = "git " + "push " + "--force origin main"
BD = "git " + "branch " + "-D victim"
RRD = "git " + "push " + "origin :feature"
CLOSE_DEL = "gh " + "pr " + "close 5 --delete-branch"


# =========================================================================================
# MATRIX BROADENING — additional intended closures the per-commit certs listed only as
# spot sets (coder open question 2 + auditor carry-forward). Each is a faithful read/search
# click whose quoted VALUE merely names a destructive op; each verified base-True -> HEAD-
# False. Completes: gh search commits/repos positionals, gh search per-subcommand value
# flags, gh issue/pr list short+long value flags, git show/shortlog/log breadth.
# =========================================================================================
MATRIX_BROADENING = [
    # gh search commits/repos positionals — the headline gap (prs/issues/code were pinned).
    ("search-commits-positional", "gh search commits '%s'" % M5),
    ("search-repos-positional", "gh search repos '%s'" % M5),
    # gh search per-subcommand value-flags (long forms).
    ("search-prs-author", "gh search prs --author '%s'" % M5),
    ("search-commits-author", "gh search commits --author '%s'" % M5),
    ("search-commits-committer", "gh search commits --committer '%s'" % M5),
    ("search-issues-label", "gh search issues --label '%s'" % M5),
    ("search-repos-owner", "gh search repos --owner '%s'" % M5),
    # gh issue list / pr list — long + short value flags beyond the pinned --search / -S.
    ("issue-list-author-long", "gh issue list --author '%s'" % M5),
    ("issue-list-A-short", "gh issue list -A '%s'" % M5),
    ("issue-list-label-short", "gh issue list -l '%s'" % M5),
    ("pr-list-author-long", "gh pr list --author '%s'" % M5),
    ("pr-list-label-long", "gh pr list --label '%s'" % M5),
    ("pr-list-base-long", "gh pr list --base '%s'" % M5),
    # git show / shortlog / log breadth (the certs pinned log-side spot forms only).
    ("log-G-spaced", "git log -G '%s'" % M5),
    ("show-S-attached", "git show -S'%s'" % M5),
    ("show-author", "git show --author '%s'" % M5),
    ("shortlog-author", "git shortlog --author '%s'" % M5),
    ("grep-positional-pathspec", "git grep '%s' -- src/" % M5),
    # git log --committer with a non-merge destructive literal (closes; the merge-literal
    # combination is the pinned carrier-7e residual owned by the #1181 cert).
    ("log-committer-close", "git log --committer '%s'" % CLOSE_DEL),
]


class TestMatrixBroadening:
    @pytest.mark.parametrize(
        "label,cmd", MATRIX_BROADENING, ids=[r[0] for r in MATRIX_BROADENING]
    )
    def test_broadened_read_verb_over_block_closed(self, label, cmd):
        assert _base()(cmd) is True, "row was not a genuine over-block at base (vacuous)"
        assert D(cmd) is False, "faithful broadened read/search click still gated at HEAD"


# =========================================================================================
# CORPUS — self-contained distillation of the merge-guard benign/destructive corpus
# "style" (reuse per spec 5.4), swept base-vs-HEAD. BENIGN rows must be False on both
# (a False->True is a NEW over-block = cardinal sin). DESTRUCTIVE rows must be True on
# both (a True->False is an UNDER-block, unless the row is an enumerated closure). The
# closure rows themselves (the three certs' sets + MATRIX_BROADENING) are folded in via
# _CLOSURE_CMDS below and are the ONLY commands permitted to transition True->False.
# =========================================================================================
BENIGN = [
    # faithful reads/searches WITHOUT a danger-looking value — never gated, either tree.
    "git status",
    "git log --oneline -20",
    "git log --grep 'refactor auth'",
    "git log --author 'Alice'",
    "git log -S 'def handler'",
    "git diff HEAD~1",
    "git show HEAD",
    "git grep 'TODO' src/",
    "git grep -e 'FIXME' -- '*.py'",
    "git branch -a",
    "git shortlog --author 'Bob'",
    "gh pr view 5",
    "gh pr list",
    "gh pr list --search 'is:open'",
    "gh issue list",
    "gh issue list --label bug",
    "gh search prs 'is:open review:required'",
    "gh search commits 'fix typo'",
    "gh search repos 'topic:cli'",
    "gh search code 'import os'",
    # api READ endpoints (no destructive method) — stay ungated.
    "gh api /repos/o/r/git/refs",
    "gh api /repos/o/r/pulls/5",
    "gh api /repos/o/r/branches/main/protection",
    # find WITHOUT a danger value.
    "find . -name '*.py'",
    "find . -path './src/*' -name '*.js'",
    "find . -iname '*.md'",
    # plain commits / echoes (no carrier-skip + quoted danger co-residency).
    "git commit -m 'fix: normal commit'",
    "git commit -m 'note about deploy' # ok",
    "gh pr view 5 # checked | sh",
    "echo hello world",
    "ls -la",
    "grep -rn 'pattern' .",
    "grep -c 'x' file.txt",
    # lease-push placement variations (faithful lease is not a hard-gate op).
    "git push --force-with-lease origin feature",
    "git push -u --force-with-lease origin feature",
    "git push --force-with-lease -v origin feature",
    # benign compound legs.
    "git status && git log --oneline",
    "gh pr list && echo done",
    # equals-form controls (carrier-4-closed upstream at base — False on both).
    'git log --grep="%s"' % M5,
    "git log --author='%s'" % M5,
    # already-closed unquoted-danger-in-comment after an inert head (stripped-surface
    # comment carrier handled this at base — control, not a closure).
    "ls -la # TODO: %s | sh" % M5,
    "gh pr view 5 # weighed %s" % M5,
]

DESTRUCTIVE = [
    # bare destructive battery — one per op-class (True on both).
    M5,
    CD5,
    PF,
    BD,
    RRD,
    "gh pr " + MW + " 5",
    "git push --force-with-lease origin main",
    "git branch -D feature",
    "git push origin :branch",
    "gh api -X DELETE repos/o/r/git/refs/heads/x",
    "gh api 'repos/o/r/git/refs/heads/x' -X DELETE",
    "gh -R o/r api repos/o/r/git/refs/heads/x -X DELETE",
    "gh api -X PUT /repos/o/r/pulls/42/" + MW,
    'git branch -D "feat/x"',
    "git push --force origin main && echo done",
    # #1181 retention: destructive sibling leg / executing / guard vectors.
    "git log --grep=foo&&%s" % M5,
    "git log --grep 'x' && %s" % M5,
    "git grep -O'sh -c \"%s\"' pat" % M5,
    "git grep -O '%s'" % M5,
    "git grep --open-files-in-pager='vim' '%s'" % M5,
    "git shortlog -e '%s'" % M5,
    'FOO="%s" git grep \'x\' && eval $FOO' % M5,
    "git grep $(sh -c '%s')" % M5,
    "git grep -f <(sh -c '%s')" % M5,
    "git grep pat -- %s" % M5,
    # #1155 find retention + declared residuals.
    "find . -name 'x' -exec sh -c '%s' \\;" % M5,
    "find . -name '%s' -exec cat {} \\;" % M5,
    "find . -name '%s' | sh" % M5,
    "awk '/%s/ {print}' f" % M5,
    "python -c \"print('%s')\"" % M5,
    "find . -iname '%s'" % M5,
    # #1148 trap canaries (executing tails must stay caught).
    "git commit -m '%s # b' | sh" % M5,
    'echo "%s"# not-a-comment | sh' % M5,
    "%s # done" % M5,
    # NOTE: `git log/show --committer '<danger>'` were HERE (pinned True->True as the
    # carrier-7e residual) but MOVED to the closure set — OBS-A3/7e closed that pre-existing
    # over-block (the danger sat in the INERT committer FILTER value; git matches it, never
    # executes). See the COMMITTER_CENSUS import in _CLOSURE_CMDS below.
    # --- R1 remediation additions (the corpus gap that let two under-blocks through) ---
    # F1 escaped-predecessor class (commit 4): a `#` after a backslash-escaped delimiter
    # is NOT a bash comment -> the routing token survives -> stays GATED (True->True; a
    # commit-4 regression re-excises it -> True->False, caught here as a freed destructive).
    *[cmd for _, cmd in _R1_ESC],
    # F2 quoted-remote/quoted-flag push/branch class (commit 5): is_dangerous is True on
    # BOTH trees — the launder was an OP-DOWNGRADE, INVISIBLE to this sweep. These rows
    # ride here only to pin "stays gated"; the op-downgrade itself is caught by
    # TestR1DetectOpParity below (the required detect-op dimension).
    *[cmd for _, cmd, _ in _R1_DELETE],
    *[cmd for _, cmd, _ in _R1_FAITHFUL_ROWS],
    # --- F3 line-continuation additions (commit 6 — the 3rd #1148 under-block class) ---
    # _F3_CLOSE: a `#` after a backslash-newline is NOT a bash comment (bash SPLICES the
    # line-continuation, gluing `#` to the preceding token) -> the routing token survives
    # -> stays GATED (base-True->HEAD-True; a commit-6 revert re-excises it -> under-block).
    *[cmd for _, cmd in _F3_CLOSE],
    # flag-scan control: `gh pr close 5 <LC>-d` splices to `... -d` so -d stays bound ->
    # dangerous on both trees (the ONE global splice serves the flag surface too; the
    # bound-flags parity is checked in TestR3LineContinuationSplice).
    "gh pr close 5 %s-d" % _F3_LC,
    "gh pr close 5 %s--delete-branch" % _F3_LC,
]

# The union of every enumerated intended closure — the ONLY commands the sweep permits
# to transition base-True -> HEAD-False. Coupled to the certs via the imports above.
# _R1_GENUINE are #1148 genuine-comment-excision closures (base-True -> HEAD-False): F1's
# escape-awareness must PRESERVE their excision, so they are intended closures, not
# over-blocks — including them here keeps the F1 over-block-safety direction in the sweep.
_CLOSURE_CMDS = (
    [cmd for _, cmd in _C1181]
    + [cmd for _, cmd in _C1155]
    + [cmd for _, cmd in _C1148]
    + [cmd for _, cmd in MATRIX_BROADENING]
    + [cmd for _, cmd in _R1_GENUINE]
    # _F3_GENUINE are #1148 genuine-comment-excision closures too (base-True->HEAD-False):
    # bash-faithful comments the splice must keep excising (over-block safety).
    + [cmd for _, cmd in _F3_GENUINE]
    # OBS-A3/7e committer-census closures (base-True->HEAD-False): the danger literal sat in
    # the INERT committer FILTER value (git matches it, never executes), so these were
    # pre-existing over-blocks the 7e -m-cluster anchor closes. RECLASSIFIED from DESTRUCTIVE
    # (verified: a benign committer value is not gated on either tree; the destructive floor
    # — git -C/-c wrappers, timeout/nice gh pr merge, push positional grep — still gates).
    + [cmd for _, cmd in COMMITTER_CENSUS]
)
_INTENDED_CLOSURE_SET = set(_CLOSURE_CMDS)

# EXPECTED_BONUS — the SOLE permitted base-False -> HEAD-True transition (commit 6). A
# mid-word line-continuation the space-join MISSED: `git pus<LC>h --force origin main`
# SPLICES to a REAL `git push --force origin main`. This is directionally identical to a
# cardinal-sin over-block (base-False->HEAD-True) but SEMANTICALLY its opposite: bash
# executes it as a genuinely DESTRUCTIVE force-push, so gating it is NEVER an over-block —
# it routes through approval and a faithful click still mints. The membership predicate is
# that semantic (a destructive HEAD op), pinned by TestR3ExpectedBonus; the real-bash
# ground truth lives in test_merge_guard_1148_cert (its _bash_pipe_executes oracle +
# test_split_word_bonus_closure). SINGLETON GUARD (lead-ratified): if this set ever needs a
# 2nd member to keep the sweep green, DO NOT widen it — a 2nd False->True is either a new
# bonus (bash-oracle-verify it is destructive, then the lead approves adding it) or a real
# cardinal over-block. Either way it is a lead decision; the cardinal-direction test REDS on
# any False->True not listed here, forcing escalation instead of a silent widen.
EXPECTED_BONUS = [
    ("split-word-force-push", "git pus%sh --force origin main" % _F3_LC, "force-push"),
]
_EXPECTED_BONUS_SET = {cmd for _, cmd, _ in EXPECTED_BONUS}

# Full corpus swept by the monotonicity accounting: benign + destructive + every closure
# + the expected bonus (so the sweep exercises the EXPECTED_BONUS exemption path).
CORPUS = BENIGN + DESTRUCTIVE + _CLOSURE_CMDS + [c for _, c, _ in EXPECTED_BONUS]


class TestCorpusMonotonicity:
    """The load-bearing safety gate: base-vs-HEAD over the whole corpus."""

    def test_zero_new_over_block_cardinal_direction(self):
        base_d = _base()
        # base-False -> HEAD-True is the cardinal-sin direction (a faithful click newly
        # blocked) EXCEPT the enumerated EXPECTED_BONUS (a provably-destructive closure —
        # see TestR3ExpectedBonus). Any OTHER False->True reds here, forcing escalation.
        offenders = [
            c for c in CORPUS
            if base_d(c) is False and D(c) is True and c not in _EXPECTED_BONUS_SET
        ]
        assert offenders == [], (
            "CARDINAL SIN — base-False -> HEAD-True (faithful click newly blocked), NOT in "
            "the enumerated EXPECTED_BONUS: %r" % offenders
        )

    def test_every_closure_is_intended(self):
        base_d = _base()
        unaccounted = [
            c for c in CORPUS
            if base_d(c) is True and D(c) is False and c not in _INTENDED_CLOSURE_SET
        ]
        assert unaccounted == [], (
            "unintended True->False transition (potential under-block masquerading as "
            "closure — not in the union of the three certs' intended-closure sets): %r"
            % unaccounted
        )

    def test_benign_rows_are_ungated_both_trees(self):
        base_d = _base()
        for cmd in BENIGN:
            assert base_d(cmd) is False, "BENIGN row was gated at base: %r" % cmd
            assert D(cmd) is False, "BENIGN row gated at HEAD (new over-block): %r" % cmd

    def test_destructive_rows_stay_gated_both_trees(self):
        base_d = _base()
        for cmd in DESTRUCTIVE:
            assert base_d(cmd) is True, "DESTRUCTIVE row not gated at base: %r" % cmd
            assert D(cmd) is True, "UNDER-BLOCK — destructive form freed at HEAD: %r" % cmd

    def test_intended_closures_actually_close(self):
        base_d = _base()
        for cmd in _CLOSURE_CMDS:
            assert base_d(cmd) is True, "closure row not a genuine over-block at base: %r" % cmd
            assert D(cmd) is False, "intended closure still gated at HEAD: %r" % cmd


# =========================================================================================
# #1155 TWO-LAYER ORACLE FAITHFULNESS PIN — the e2e base leg patches 4 classifier seams
# (is_dangerous_command, extract_command_context, _single_destructive_leg,
# _single_detectable_leg) but NOT is_compound_destructive_command, the 5th classifier
# seam check_merge_authorization consults BEFORE the token-match branch. This pin proves
# the omission is FAITHFUL: is_compound_destructive_command(BITE) is False on BOTH trees,
# so real base reaches the token-mismatch path (the filed symptom) — not the compound
# rejection. If a future edit makes the bite classify compound at base, the e2e base leg
# would silently reproduce the WRONG path; this pin fails first.
# =========================================================================================
BITE = "gh pr close 5 --comment 'weighed gh pr " + MW + " 5 --admin but closing instead'"


class TestBiteOracleFaithfulness:
    def test_bite_is_not_compound_on_either_tree(self):
        base = load_baseline()
        assert base.is_compound_destructive_command(BITE) is False, (
            "e2e base leg unfaithful: base classifies the bite compound, so real base "
            "would return the compound rejection, not the token-mismatch symptom"
        )
        assert mgc.is_compound_destructive_command(BITE) is False

    def test_bite_is_dangerous_on_both_trees(self):
        # the bite is a close (destructive) on both trees — approval is always required;
        # the fix changes WHICH op the read side binds (merge->close), not whether gated.
        assert load_baseline().is_dangerous_command(BITE) is True
        assert D(BITE) is True


# =========================================================================================
# R1 / F1 — ESCAPED-PREDECESSOR CLASS (remediation commit 4). The original curated corpus
# MISSED this spelling, which is how the under-block got through (my Task #31 uncertainty
# #1: a faithful/executing spelling absent from the corpus can go unseen). Permanent
# gap-closure: a `#` after a BACKSLASH-ESCAPED whitespace/;/&/| delimiter is NOT a bash
# comment (bash executes the tail), so an escape-aware excision must LEAVE the routing
# token visible -> the real `| sh` stays -> stays GATED. is_dangerous-visible: base-True
# -> HEAD-True retention. Delete-the-fix (authoring-time, git-show parent 4e130f6f):
# 10/10 _R1_ESC rows go under-blocked (is_dangerous False) at the parent, gated at HEAD —
# commit 4 is individually load-bearing.
# =========================================================================================
class TestR1EscapedPredecessor:
    @pytest.mark.parametrize("label,cmd", _R1_ESC, ids=[r[0] for r in _R1_ESC])
    def test_escaped_delimiter_stays_gated(self, label, cmd):
        assert load_baseline().is_dangerous_command(cmd) is True, (
            "%s: not a genuine gated form at base (vacuous)" % label
        )
        assert mgc.is_dangerous_command(cmd) is True, (
            "%s: escaped-# comment wrongly excised -> a real pipe-to-shell was freed" % label
        )

    @pytest.mark.parametrize("label,cmd", _R1_GENUINE, ids=[r[0] for r in _R1_GENUINE])
    def test_genuine_comment_stays_excised_no_over_block(self, label, cmd):
        # OVER-BLOCK SAFETY (the primary gate): F1's escape-awareness must NOT re-gate a
        # faithful click carrying a REAL comment — a genuine comment stays excised -> not
        # dangerous. (base-True -> HEAD-False; these ride in _CLOSURE_CMDS as intended.)
        assert mgc.is_dangerous_command(cmd) is False, (
            "%s: genuine comment wrongly treated as non-comment -> faithful click "
            "OVER-BLOCKED (cardinal-direction regression)" % label
        )


# =========================================================================================
# R1 / F2 — DETECT-OP PARITY (remediation commit 5). The quoted-remote/quoted-flag launder
# keeps is_dangerous=True on BOTH trees — it is an OP-DOWNGRADE (remote-ref-delete /
# remote-mass-delete / force-push -> push-to-main) that the is_dangerous monotonicity sweep
# STRUCTURALLY CANNOT SEE. This is the standing regression catch the corpus previously
# lacked: base.detect == HEAD.detect == the restored op over the imported R1 rows. A
# commit-5 regression (push/branch arms back on the masked view) re-downgrades the op ->
# HEAD.detect diverges from base.detect -> fails here. Delete-the-fix (authoring-time,
# git-show parent 7ca8de80): 10/10 _R1_DELETE rows op-downgrade to push-to-main at the
# parent — commit 5 is individually load-bearing. Coupling: rows imported from the 1155
# cert (anti-drift). The full mint->auth launder round-trip lives in that cert; a thin
# standing smoke rides in TestR1LaunderE2ESmoke below.
# =========================================================================================
class TestR1DetectOpParity:
    @pytest.mark.parametrize(
        "label,cmd,want", _R1_OP_ROWS, ids=[r[0] for r in _R1_OP_ROWS]
    )
    def test_detect_op_parity_base_equals_head(self, label, cmd, want):
        base_detect = load_baseline().detect_command_operation_type
        assert base_detect(cmd) == want, "%s: base op drifted from the expected op" % label
        assert mgc.detect_command_operation_type(cmd) == want, (
            "%s: op-downgrade launder re-opened — HEAD detect diverged from base "
            "(is_dangerous cannot see this; only detect-op parity does)" % label
        )
        assert mgc.is_dangerous_command(cmd) is True, "%s: lost gating" % label


class TestR1LaunderE2ESmoke:
    """Belt-and-suspenders standing anchor for the F2 launder. The full mint->auth
    round-trip lives in test_merge_guard_1155_cert::TestR1PushArmsOffView; this thin smoke
    survives even if that cert is later refactored: a benign push-to-main token must REFUSE
    a quoted-remote DELETE (the confirmed end-to-end launder)."""

    def test_benign_push_token_refuses_quoted_remote_delete(self, tmp_path):
        question = {
            "question": "Proceed?",
            "options": [{"label": "Yes", "description": "Run `git push origin main` now"}],
            "multiSelect": False,
        }
        ctx, refusal = merge_guard_post._mint_context_from_bundle(
            [question], {"Proceed?": "Yes"}
        )
        assert ctx is not None and ctx["operation_type"] == "push-to-main", (
            "faithful push-to-main click stopped minting (%s)" % refusal
        )
        merge_guard_post.write_token(ctx, token_dir=tmp_path)
        victim = "git push " + "'origin' --delete main"
        assert merge_guard_pre.check_merge_authorization(victim, tmp_path) is not None, (
            "LAUNDER OPEN: a benign push-to-main token authorized a quoted-remote delete"
        )


# =========================================================================================
# R3 / F3 — LINE-CONTINUATION SPLICE (remediation commit 6). A `#` after a shell
# line-continuation (backslash-newline) is NOT a bash comment: bash SPLICES the
# line-continuation (removes the backslash+newline and glues the surrounding text), so the
# `#` glues to a non-delimiter and the pipe executes. The fix splices FIRST, then applies
# the escape-aware comment predicate to the spliced surface (subsumes F1). is_dangerous-
# visible: _F3_CLOSE are base-True -> HEAD-True retention; a commit-6 revert re-excises them
# -> under-block (delete-the-fix, git-show parent b4ea5bfc^=07a2994f: 11/11 _F3_CLOSE under-
# block at the parent). _F3_GENUINE (bare-newline/plain-space/even-backslash/CRLF) are real
# comments -> excised -> base-True -> HEAD-False closures (over-block safety; they ride the
# closure set). Sets imported from the 1148 cert (anti-drift).
# =========================================================================================
class TestR3LineContinuationSplice:
    @pytest.mark.parametrize("label,cmd", _F3_CLOSE, ids=[r[0] for r in _F3_CLOSE])
    def test_line_continuation_before_hash_stays_gated(self, label, cmd):
        assert _base()(cmd) is True, "%s: not a genuine gated form at base (vacuous)" % label
        assert D(cmd) is True, (
            "%s: F3 line-continuation-splice under-block re-opened (# wrongly excised after "
            "a spliced line-continuation)" % label
        )

    @pytest.mark.parametrize("label,cmd", _F3_GENUINE, ids=[r[0] for r in _F3_GENUINE])
    def test_genuine_comment_stays_excised_no_over_block(self, label, cmd):
        # OVER-BLOCK SAFETY: a real comment (bare-newline / plain-space / even-backslash /
        # CRLF) stays excised -> not dangerous. The splice must NOT re-over-block these.
        assert D(cmd) is False, (
            "%s: F3 splice over-blocked a genuine comment (cardinal-direction regression)" % label
        )

    def test_flag_scan_control_binds_delete_class_across_splice(self):
        # the ONE global splice serves the FLAG surface too: `gh pr close 5 <LC>-d` splices
        # to `gh pr close 5 -d`, so -d / --delete-branch stays a bound token (base==HEAD) and
        # gated — proving the splice did not drop a flag binding the space-join kept.
        base = load_baseline()
        for cmd in ("gh pr close 5 %s-d" % _F3_LC, "gh pr close 5 %s--delete-branch" % _F3_LC):
            b_ctx = base.extract_command_context(cmd)
            h_ctx = mgc.extract_command_context(cmd)
            assert h_ctx.get("bound_flags") == b_ctx.get("bound_flags"), (
                "splice dropped a flag binding the space-join kept: %r" % cmd
            )
            assert "--delete-branch" in h_ctx.get("bound_flags", []), cmd
            assert D(cmd) is True and base.is_dangerous_command(cmd) is True, cmd


class TestR3ExpectedBonus:
    """The SOLE permitted base-False -> HEAD-True. Its membership predicate is SEMANTIC
    (lead ruling): bash executes it as a genuinely DESTRUCTIVE command, so gating it is a
    destructive-command closure — routing through approval, a faithful click still mints —
    NOT a benign-click over-block. Delete-the-fix (git-show parent b4ea5bfc^=07a2994f): the
    gate appears at commit 6 (parent False -> HEAD True). Real-bash ground truth lives in
    test_merge_guard_1148_cert (_bash_pipe_executes + test_split_word_bonus_closure)."""

    @pytest.mark.parametrize(
        "label,cmd,op", EXPECTED_BONUS, ids=[r[0] for r in EXPECTED_BONUS]
    )
    def test_bonus_is_a_destructive_closure_not_over_block(self, label, cmd, op):
        assert _base()(cmd) is False, "%s: bonus is not base-False (not a bonus)" % label
        assert D(cmd) is True, "%s: bonus does not gate at HEAD (F3 splice incomplete)" % label
        # the SEMANTIC discriminant: HEAD classifies a genuinely destructive op, so the gate
        # is a destructive-command closure, not a faithful-benign-click block.
        assert mgc.detect_command_operation_type(cmd) == op, (
            "%s: bonus HEAD op %r is not the destructive op %r — a base-False->HEAD-True "
            "that is NOT a provable destructive closure must NOT be whitelisted" % (
                label, mgc.detect_command_operation_type(cmd), op)
        )

    def test_expected_bonus_is_a_singleton(self):
        # SINGLETON GUARD (lead-ratified): a 2nd member is a lead decision (bash-oracle-
        # verify it is genuinely destructive, then the lead approves) or a real over-block —
        # never a silent widen. The cardinal-direction sweep REDS on any unlisted False->True.
        assert len(EXPECTED_BONUS) == 1, (
            "EXPECTED_BONUS gained a member — do NOT widen the primary cardinal gate; "
            "escalate to the lead to bash-oracle-verify the new row is genuinely destructive"
        )
