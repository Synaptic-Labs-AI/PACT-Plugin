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
    # carrier-7e known residual: committer x merge-literal stays True (pinned by #1181).
    "git log --committer '%s'" % M5,
    "git show --committer '%s'" % M5,
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
)
_INTENDED_CLOSURE_SET = set(_CLOSURE_CMDS)

# Full corpus swept by the monotonicity accounting: benign + destructive + every closure.
CORPUS = BENIGN + DESTRUCTIVE + _CLOSURE_CMDS


class TestCorpusMonotonicity:
    """The load-bearing safety gate: base-vs-HEAD over the whole corpus."""

    def test_zero_new_over_block_cardinal_direction(self):
        base_d = _base()
        offenders = [c for c in CORPUS if base_d(c) is False and D(c) is True]
        assert offenders == [], (
            "CARDINAL SIN — base-False -> HEAD-True (faithful click newly blocked): %r"
            % offenders
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
