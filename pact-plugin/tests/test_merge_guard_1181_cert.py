"""
Location: pact-plugin/tests/test_merge_guard_1181_cert.py
Summary: BIDIRECTIONAL certification for the read-verb value over-block class — the
         `_strip_read_verb_values` carve-out (pre-d2 in `_maybe_strip_leg`). Certifies
         against the REAL `is_dangerous_command`, BASE (committed vendored fixture via
         merge_guard_baseline_loader — loud-fail, CI-executable, never skip) vs HEAD
         (live worktree module). NEVER a byte-diff / additive-lines argument.

         THREAT POLARITY (SACROSANCT merge_guard control):
           - OVER-block = cardinal sin: a faithful read-verb search click
             (`git log --grep "…"`, `git grep '…'`, `gh pr list --search '…'`) whose
             quoted VALUE merely names a destructive op must never gate. CLOSURE rows
             certify base-True -> HEAD-False.
           - UNDER-block = the hole the fix must not open. Executing values
             (git grep -O pager), expanded/eval'd values (g2), command/process
             substitution (g3/g4), destructive sibling legs, and unquoted in-leg danger
             MUST stay caught: RETENTION rows certify base-True -> HEAD-True with the
             danger placed so a wrong strip flips them False.

         NON-VACUITY: every closure row asserts BASE(cmd) is True (the form was a
         genuine over-block at the baseline) AND HEAD(cmd) is False. Every retention
         row asserts True on BOTH. `=form` rows are FALSE on both (carrier-4-closed
         upstream — controls proving this carve-out does NOT re-handle them).

         KNOWN RESIDUAL (documented, pre-existing — NOT a closure failure of this fix):
         `git log/show --committer '<value containing " merge ">'` stays True on HEAD.
         The git-merge span carrier's prefix word class crosses quote boundaries, so a
         `merge` INSIDE the quoted value heads a span over the leg, and that carrier's
         short `-m` cluster arm (no token-start lookbehind) rewrites `--com|mitter` ->
         `--comSTRIPPED` BEFORE the leg walk runs — the carve-out's flag arm can then no
         longer anchor. Verified byte-identical corruption on the BASE classifier
         (pre-existing interaction, not introduced here). Pinned below as a residual so
         a future fix of that span prefix flips the pin deliberately.

         Destructive verbs are assembled at runtime so this file carries no raw
         force-delete / force-push / merge literal and stays inert to the live guard.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

import shared.merge_guard_common as mgc  # noqa: E402
from merge_guard_baseline_loader import load_baseline  # noqa: E402

D = mgc.is_dangerous_command


def _base():
    return load_baseline().is_dangerous_command


# --- Destructive literals assembled at runtime (inert to the live guard) ---
M5 = "gh " + "pr " + "merge 5 --admin"            # merge + privileged flag
CD5 = "gh " + "pr " + "close 5 --delete-branch"   # destructive close
PF = "git " + "push " + "--force origin main"     # force-push
BD = "git " + "branch " + "-D victim"             # force branch-delete
RRD = "git " + "push " + "origin :feature"        # remote ref delete

# =========================================================================================
# CLOSURE — base-True -> HEAD-False. The intended-closure set: space-form value flags,
# attached/spaced -S/-G shorts, git grep positional/-e/-f, gh search positionals,
# gh issue/pr list --search (long + short). Each value is a quoted destructive literal,
# proving the danger-looking VALUE alone no longer gates the faithful read click.
# =========================================================================================
INTENDED_CLOSURES = [
    ("log-grep-space-dq", 'git log --grep "%s"' % M5),
    ("log-grep-space-sq", "git log --grep '%s'" % M5),
    ("log-grep-reflog", "git log --walk-reflogs --grep-reflog '%s'" % M5),
    ("log-author", "git log --author '%s'" % M5),
    # --committer closes for non-merge literals; the merge-literal combination is the
    # documented pre-existing residual pinned in TestKnownResiduals.
    ("log-committer-branchD", "git log --committer '%s'" % BD),
    ("log-committer-forcepush", "git log --committer '%s'" % PF),
    ("log-S-attached", "git log -S'%s'" % M5),
    ("log-G-attached", "git log -G'%s'" % M5),
    ("log-S-spaced", "git log -S '%s'" % M5),
    ("show-grep", 'git show --grep "%s"' % M5),
    ("shortlog-grep", 'git shortlog --grep "%s"' % M5),
    ("grep-positional", "git grep '%s'" % M5),
    ("grep-e", "git grep -e '%s'" % M5),
    ("grep-f-filename", "git grep -f '%s' src/" % M5),
    ("search-prs-positional", "gh search prs '%s'" % M5),
    ("search-issues-positional", "gh search issues '%s'" % M5),
    ("search-code-positional", "gh search code '%s'" % M5),
    ("issue-list-search", "gh issue list --search '%s'" % M5),
    ("pr-list-search", "gh pr list --search '%s'" % M5),
    ("pr-list-S-short", "gh pr list -S '%s'" % M5),
]


class TestClosure:
    @pytest.mark.parametrize(
        "label,cmd", INTENDED_CLOSURES, ids=[r[0] for r in INTENDED_CLOSURES]
    )
    def test_read_verb_value_over_block_closed(self, label, cmd):
        assert _base()(cmd) is True, "row was not a genuine over-block at base (vacuous)"
        assert D(cmd) is False, "faithful read-verb click still gated at HEAD"


# =========================================================================================
# RETENTION — base-True -> HEAD-True. Danger placed where a wrong strip would flip the
# row False: destructive sibling legs (separator discipline), bare destructive ops
# (one per op-class), the git grep -O executing-flag deny, the shortlog -e boolean
# collision, the g2/g3/g4 guard vectors verbatim, and unquoted in-leg danger.
# =========================================================================================
RETENTION = [
    # separator discipline: the strip is per-leg BY CONSTRUCTION — the destructive
    # sibling leg is never in the strip's input string.
    ("glued-separator", "git log --grep=foo&&%s" % M5),
    ("spaced-separator", "git log --grep 'x' && %s" % M5),
    # destructive-op battery (one row per op-class): a bare faithful destructive click
    # must keep minting/gating exactly as before.
    ("bare-merge-admin", M5),
    ("bare-close-delete-branch", CD5),
    ("bare-force-push", PF),
    ("bare-branch-delete", BD),
    ("bare-remote-ref-delete", RRD),
    # git grep -O family EXECUTES its attached value -> whole-leg deny (g6).
    ("grep-O-attached-deny", "git grep -O'sh -c \"%s\"' pat" % M5),
    ("grep-O-bare-deny", "git grep -O '%s'" % M5),
    ("grep-open-files-in-pager-deny", "git grep --open-files-in-pager='vim' '%s'" % M5),
    # -e collision pair, retention half: shortlog's -e is the BOOLEAN --email — its spec
    # has no -e arm and no positional strip, so the quoted danger survives (per-verb
    # keying documented; a shared short-flag table would have consumed it).
    ("shortlog-e-boolean", "git shortlog -e '%s'" % M5),
    # g2: eval present -> carrier 4 skips assignments -> the NAME="…" survivor is an
    # expanded value executed by the sibling leg; the carve-out must fall through.
    ("g2-eval-assignment", 'FOO="%s" git grep \'x\' && eval $FOO' % M5),
    # g3: a bare $() in the leg executes its content.
    ("g3-command-substitution", "git grep $(sh -c '%s')" % M5),
    # g4: an unquoted process substitution executes its content.
    ("g4-process-substitution", "git grep -f <(sh -c '%s')" % M5),
    # unquoted danger tokens in-leg stay visible (only quoted spans / flag values strip).
    ("unquoted-in-leg-danger", "git grep pat -- %s" % M5),
]


class TestRetention:
    @pytest.mark.parametrize("label,cmd", RETENTION, ids=[r[0] for r in RETENTION])
    def test_destructive_and_executing_forms_stay_caught(self, label, cmd):
        assert _base()(cmd) is True, "retention row not True at base (vacuous)"
        assert D(cmd) is True, "fix opened an under-block: destructive form freed"


# =========================================================================================
# CONTROLS — base-False AND HEAD-False. The `--flag=VALUE` form is carrier-4-closed
# UPSTREAM; the carve-out's long arms take `\s+` (space form only) and MUST NOT
# re-handle the equals form.
# =========================================================================================
EQUALS_FORM_CONTROLS = [
    ("log-grep-equals-dq", 'git log --grep="%s"' % M5),
    ("log-author-equals-sq", "git log --author='%s'" % M5),
]


class TestEqualsFormControls:
    @pytest.mark.parametrize(
        "label,cmd", EQUALS_FORM_CONTROLS, ids=[r[0] for r in EQUALS_FORM_CONTROLS]
    )
    def test_equals_form_already_closed_upstream(self, label, cmd):
        assert _base()(cmd) is False, "=form was not carrier-4-closed at base"
        assert D(cmd) is False, "=form regressed at HEAD"


# =========================================================================================
# KNOWN RESIDUAL — pre-existing carrier interaction (see module docstring). Pinned
# True->True so a future fix of the merge-span prefix flips this row DELIBERATELY.
# =========================================================================================
class TestKnownResiduals:
    @pytest.mark.parametrize(
        "label,cmd",
        [
            ("log-committer-merge-literal", "git log --committer '%s'" % M5),
            ("show-committer-merge-literal", "git show --committer '%s'" % M5),
        ],
        ids=["log-committer-merge-literal", "show-committer-merge-literal"],
    )
    def test_committer_merge_literal_residual(self, label, cmd):
        assert _base()(cmd) is True
        assert D(cmd) is True, (
            "residual unexpectedly closed — if the git-merge span prefix was fixed, "
            "move this row to INTENDED_CLOSURES deliberately"
        )


# =========================================================================================
# STRUCTURAL NEGATIVE CONTROLS — booleans are excluded BY OMISSION; listing one would
# consume the next real token (the curl/wget-shaped fail-open). Assert the known
# booleans appear in NO flag_arm and the verb set stays CLOSED.
# =========================================================================================
class TestStructuralNegatives:
    _KNOWN_BOOLEANS = ["--invert-grep", "--all-match", "--web", "--draft", "--email"]

    def test_no_boolean_in_any_flag_arm(self):
        for key, spec in mgc._READ_VERB_SPECS.items():
            if spec.flag_arm is None:
                continue
            for boolean in self._KNOWN_BOOLEANS:
                assert boolean not in spec.flag_arm.pattern, (
                    "arity-0 flag %s found in flag_arm for %r — it would consume the "
                    "next real token" % (boolean, key)
                )

    def test_verb_set_is_closed(self):
        assert ("git", "diff") not in mgc._READ_VERB_SPECS
        assert ("git", "push") not in mgc._READ_VERB_SPECS
        assert ("git", "branch") not in mgc._READ_VERB_SPECS

    def test_shortlog_has_no_short_flags(self):
        # the -e (grep: value) vs --email (shortlog: boolean) collision — per-verb keying.
        arm = mgc._READ_VERB_SPECS[("git", "shortlog")].flag_arm.pattern
        assert "-[SG]" not in arm and "-[ef]" not in arm and "-e" not in arm

    def test_flag_arms_are_single_capture_group(self):
        # the _strip_flag_values flag_sep contract: exactly ONE capturing group.
        for key, spec in mgc._READ_VERB_SPECS.items():
            if spec.flag_arm is not None:
                assert spec.flag_arm.groups == 1, key


# =========================================================================================
# MONOTONICITY ACCOUNTING over this file's row corpus: the base->HEAD transition set is
# EXACTLY {True->False for INTENDED_CLOSURES}; NO row anywhere transitions False->True.
# (The corpus-wide sweep expansion is TEST-phase work; this accounting covers every row
# this cert introduces.)
# =========================================================================================
class TestMonotonicityAccounting:
    def test_no_false_to_true_and_closures_accounted(self):
        base_d = _base()
        all_rows = (
            INTENDED_CLOSURES + RETENTION + EQUALS_FORM_CONTROLS
            + [
                ("log-committer-merge-literal", "git log --committer '%s'" % M5),
                ("show-committer-merge-literal", "git show --committer '%s'" % M5),
            ]
        )
        closure_labels = {r[0] for r in INTENDED_CLOSURES}
        for label, cmd in all_rows:
            b, h = base_d(cmd), D(cmd)
            assert not (b is False and h is True), (
                "False->True transition (new over-block) on %s" % label
            )
            if b is True and h is False:
                assert label in closure_labels, (
                    "unintended True->False transition (closure not in the intended "
                    "set — potential under-block masquerading as closure) on %s" % label
                )
