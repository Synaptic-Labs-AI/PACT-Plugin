"""
Location: pact-plugin/tests/test_merge_guard_obs_cert.py
Summary: GOOD-FAITH over-block sweep certification (PR #1195 OBS). Certifies against the
         REAL classifier, base (committed vendored fixture via merge_guard_baseline_loader
         — loud-fail, CI-executable) vs live HEAD. NEVER a byte-diff / git-show-by-SHA.

         GOVERNING MODEL — GOOD-FAITH: OVER-BLOCK = cardinal, always fix (census rows must
         CLOSE, base-True -> HEAD-False). UNDER-BLOCK = acceptable IFF it requires
         deliberate/adversarial construction (a good-faith user could never accidentally
         type it); a good-faith DESTRUCTIVE command slipping through unguarded is STILL
         unacceptable. Every section names (a) the census over-blocks it closes and (b)
         the DESTRUCTIVE-STILL-GATES floor that MUST stay is_dangerous=True.

         THE LOAD-BEARING GUARD — the destructive-still-gates floor. The read-verb strip
         and the -m carriers only ever remove STRUCTURALLY-INERT filter/message values
         (git/gh match or print them, never shell-execute), and the strip fires ONLY when
         the resolved head is a read verb from the CLOSED set — so a good-faith destructive
         command is never touched and STILL GATES. The floor rows below are the sole
         regression tripwire; ANY of them flipping to not-dangerous is a good-faith
         under-block (STOP-and-report, NOT good-faith-acceptable).

         Destructive verbs assembled at runtime so this file stays inert to the live guard.
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

import merge_guard_post as mgpost  # noqa: E402
import merge_guard_pre as mgpre  # noqa: E402
import shared.merge_guard_common as mgc  # noqa: E402
from merge_guard_baseline_loader import load_baseline  # noqa: E402
from merge_guard_post import main as post_main  # noqa: E402
from merge_guard_pre import main as pre_main  # noqa: E402

D = mgc.is_dangerous_command
DANGER = "gh " + "pr " + "merge 5 --admin"


def _base():
    return load_baseline().is_dangerous_command


# =========================================================================================
# DESTRUCTIVE-STILL-GATES FLOOR — the good-faith guard. Shared across every OBS section.
# ANY row flipping to not-dangerous is a good-faith under-block (STOP-and-report).
# =========================================================================================
FLOOR = [
    ("git -C prefix + branch -D", "git -C /p branch -D main"),
    ("git -c config + branch -D", "git -c user.name=x branch -D main"),
    ("git -c core.pager + force-push", "git -c core.pager=x push --force origin main"),
    ("timeout wrapper + gh pr merge", "timeout 5 gh pr merge 5 --admin"),
    ("nice wrapper + gh pr merge", "nice gh pr merge 5 --admin"),
    ("push positional named grep", "git push origin grep ':main'"),
]


class TestDestructiveStillGatesFloor:
    @pytest.mark.parametrize("label,cmd", FLOOR, ids=[r[0] for r in FLOOR])
    def test_floor_row_stays_gated(self, label, cmd):
        assert _base()(cmd) is True, "floor row not gated at base (vacuous): %r" % cmd
        assert D(cmd) is True, (
            "FLOOR VIOLATION (good-faith under-block) — destructive form freed at HEAD: %r"
            % cmd
        )


# =========================================================================================
# OBS-A3 / 7e — carrier `-m` cluster arm TOKEN-START anchor `(?<!\S)` (commit 1).
# CENSUS: `git log/show/shortlog --committer '<inert value containing "merge">'` was an
# over-block because carrier-7e's git-merge span mis-matched `-com` inside `--committer`
# and mangled the surface. The anchor stops the mid-flag mis-match; the read-verb strip
# then cleans the inert committer value -> not-dangerous.
# =========================================================================================
COMMITTER_CENSUS = [
    ("log-committer", "git log --committer '%s'" % DANGER),
    ("show-committer", "git show --committer '%s'" % DANGER),
    ("shortlog-committer", "git shortlog --committer '%s'" % DANGER),
]

# STRICT SACROSANCT no-regression — genuine `-m` message carriers still strip to
# not-dangerous. `git commit -am` is the load-bearing one: the token-start `-am` cluster
# the anchor MUST still admit (it is whitespace-preceded). These are base-False already
# (the #1181/#1129 carriers stripped them) -> must STAY not-dangerous (no NEW over-block).
MESSAGE_NO_REGRESSION = [
    ("commit -m", "git commit -m '%s'" % DANGER),
    ("commit -am (token-start cluster)", "git commit -am '%s'" % DANGER),
    ("commit --message=", "git commit --message='%s'" % DANGER),
    ("tag -m", "git tag -m '%s' v1" % DANGER),
    ("stash push -m", "git stash push -m '%s'" % DANGER),
]


class TestObsA3CommitterAnchor:
    @pytest.mark.parametrize(
        "label,cmd", COMMITTER_CENSUS, ids=[r[0] for r in COMMITTER_CENSUS]
    )
    def test_committer_over_block_closes(self, label, cmd):
        assert _base()(cmd) is True, "not a genuine over-block at base (vacuous)"
        assert D(cmd) is False, "the --committer inert read search still gated at HEAD"

    @pytest.mark.parametrize(
        "label,cmd", MESSAGE_NO_REGRESSION, ids=[r[0] for r in MESSAGE_NO_REGRESSION]
    )
    def test_genuine_message_no_new_over_block(self, label, cmd):
        # STRICT bar (good-faith relaxation does NOT apply to the over-block direction):
        # the anchor must not stop a genuine token-start -m/-am/--message= from stripping.
        assert D(cmd) is False, (
            "NEW over-block on the -m path — the (?<!\\S) anchor wrongly blocked a genuine "
            "message strip: %r" % cmd
        )

    def test_anchor_present_in_both_sites(self):
        # L337 _MSG_FLAG_ANCHOR (census-load-bearing) + carrier-5 inline copy (defensive).
        assert "(?<!\\S)-[a-ln-zA-Z]*m" in mgc._MSG_FLAG_ANCHOR


# =========================================================================================
# OBS-A1 — prefix/wrapper-aware read-verb resolution (commit 2). git GLOBAL-flag prefixes
# (`git -C /p log`, `git -c k=v log`, `git --no-pager log`) and read-only WRAPPER prefixes
# (`timeout 5 git log`, `nice git log`) previously key-missed -> fell to d2 wholesale
# preserve -> over-block. _resolve_git_subcommand skips git globals to the real verb;
# _wrapper_nested_command + recursion resolves the wrapped read verb.
# =========================================================================================
A1_CENSUS = [
    ("git-C-prefix-log-grep", "git -C /p log --grep '%s'" % DANGER),
    ("git-c-config-log-grep", "git -c core.pager=x log --grep '%s'" % DANGER),
    ("git-no-pager-log-grep", "git --no-pager log --grep '%s'" % DANGER),
    ("git-C-grep-e", "git -C /p grep -e '%s'" % DANGER),
    ("timeout-wrapper-log-grep", "timeout 5 git log --grep '%s'" % DANGER),
    ("nice-wrapper-log-grep", "nice git log --grep '%s'" % DANGER),
    ("timeout-wrapper-grep-positional", "timeout 5 git grep '%s'" % DANGER),
]

# OBS-A2 — bundled-cluster pickaxe short + find -iname/-ipath/-regex/-iregex.
A2_CENSUS = [
    ("bundled-nS-separate", "git log -n5 -S'%s'" % DANGER),
    ("bundled-nS-attached", "git log -nS'%s'" % DANGER),
    ("bundled-wG", "git log -wG'%s'" % DANGER),
    ("find-iname", "find . -iname '%s'" % DANGER),
    ("find-ipath", "find . -ipath '%s'" % DANGER),
    ("find-regex", "find . -regex '%s'" % DANGER),
    ("find-iregex", "find . -iregex '%s'" % DANGER),
]

# Retention under the broadenings: executing/deny forms MUST still gate.
A1A2_RETENTION = [
    ("git-C-grep-O-deny", "git -C /p grep -O'vim' '%s'" % DANGER),
    ("wrapper-grep-procsub", "timeout 5 git grep -f <(sh -c '%s')" % DANGER),
    ("find-iname-exec-deny", r"find . -iname '%s' -exec cat {} \;" % DANGER),
]


class TestObsA1PrefixWrapperResolution:
    @pytest.mark.parametrize("label,cmd", A1_CENSUS, ids=[r[0] for r in A1_CENSUS])
    def test_prefix_wrapper_read_search_closes(self, label, cmd):
        assert _base()(cmd) is True, "not a genuine over-block at base (vacuous)"
        assert D(cmd) is False, "prefix/wrapper read search still gated at HEAD"

    def test_resolver_stops_at_first_positional(self):
        # the load-bearing invariant: a destructive subcommand resolves to ITSELF, never
        # gobbling a positional word into a read-verb key.
        assert mgc._resolve_git_subcommand(["git", "push", "origin", "grep", ":main"]) == 1
        assert mgc._resolve_git_subcommand(["git", "-C", "/p", "branch", "-D", "x"]) == 3
        assert mgc._resolve_git_subcommand(["git", "-c", "k=v", "log"]) == 3
        assert mgc._resolve_git_subcommand(["git", "--foo", "log"]) is None  # unknown -> None


class TestObsA2VocabBroadening:
    @pytest.mark.parametrize("label,cmd", A2_CENSUS, ids=[r[0] for r in A2_CENSUS])
    def test_bundled_short_and_find_pattern_close(self, label, cmd):
        assert _base()(cmd) is True, "not a genuine over-block at base (vacuous)"
        assert D(cmd) is False, "bundled-short / find match-pattern search still gated"


class TestObsA1A2Retention:
    @pytest.mark.parametrize("label,cmd", A1A2_RETENTION, ids=[r[0] for r in A1A2_RETENTION])
    def test_executing_and_deny_forms_stay_gated(self, label, cmd):
        assert _base()(cmd) is True
        assert D(cmd) is True, "the broadening opened an under-block on an executing form"


# =========================================================================================
# OBS-A3g — LAZY git-merge span gobbler (commit 3). "merge" is uniquely BOTH a carrier verb
# AND a substring of the `gh pr merge` danger pattern, so the GREEDY `{0,N}` prefix gobbler
# crossed into a `-m` message VALUE containing "merge" and anchored on the INNER merge,
# leaving the danger visible (over-block). The one-char `{0,N}?` (lazy) anchors on the VERB
# so the `-m` value strips cleanly. This is a SACROSANCT danger-detection change held to the
# STRICT bar: no new over-block AND no ungated good-faith destructive command (leg-locality).
# =========================================================================================
A3G_CLOSURE = [
    ("merge-m", "git merge -m '%s'" % DANGER),
    ("merge-branch-m", "git merge feature -m '%s'" % DANGER),
    ("merge-no-ff-m", "git merge --no-ff feature -m '%s'" % DANGER),
    ("merge-multiple-m", "git merge -m 'note' -m '%s'" % DANGER),
    ("merge-c-mergetool", "git -c merge.tool=x merge -m '%s'" % DANGER),
    ("merge-c-quoted", "git -c 'user.name=A B' merge -m '%s'" % DANGER),
    ("merge-C-prefix", "git -C /p merge -m '%s'" % DANGER),
]

# MUST-STILL-GATE — a real destructive op in a SEPARATE leg is NOT swallowed (the lazy
# change touches only WHERE `merge` matches, not the _VERB_MSG_BODY leg-locality terminator).
_BD = "git " + "branch " + "-D main"
_PF = "git " + "push " + "--force origin main"
A3G_COMPOUND_GATES = [
    ("merge-then-gh-merge-amp", "git merge -m 'note' && %s" % DANGER),
    ("merge-then-force-push-semi", "git merge -m 'note' ; %s" % _PF),
    ("merge-then-gh-merge-pipe", "git merge -m 'note' | %s" % DANGER),
    ("merge-then-branch-delete-amp", "git merge -m 'note' && %s" % _BD),
]

# CONTROLS — no-message merge unchanged; sibling message carriers (commit/tag/stash) stay
# GREEDY and were never affected (no danger pattern contains their verb word).
A3G_CONTROLS = [
    ("commit-m-mentions-merge", "git commit -m 'commit the %s fix'" % DANGER, False),
    ("tag-m-mentions-merge", "git tag -m 'tag before %s' v1" % DANGER, False),
    ("stash-m-mentions-merge", "git stash push -m 'stash the %s wip'" % DANGER, False),
    ("merge-no-message", "git merge feature", False),
]


class TestObsA3gLazyMergeSpan:
    @pytest.mark.parametrize("label,cmd", A3G_CLOSURE, ids=[r[0] for r in A3G_CLOSURE])
    def test_merge_message_over_block_closes(self, label, cmd):
        assert _base()(cmd) is True, "not a genuine over-block at base (vacuous)"
        assert D(cmd) is False, "git merge -m '<danger>' still gated at HEAD"

    @pytest.mark.parametrize(
        "label,cmd", A3G_COMPOUND_GATES, ids=[r[0] for r in A3G_COMPOUND_GATES]
    )
    def test_separate_destructive_leg_still_gates(self, label, cmd):
        # STRICT SACROSANCT: the lazy change must NOT ungate a real destructive sibling leg.
        assert D(cmd) is True, (
            "leg-locality broken — a real destructive op in a separate leg was swallowed: %r"
            % cmd
        )

    @pytest.mark.parametrize(
        "label,cmd,want", A3G_CONTROLS, ids=[r[0] for r in A3G_CONTROLS]
    )
    def test_controls_unchanged(self, label, cmd, want):
        assert D(cmd) is want, "control changed under the lazy git-merge span: %r" % cmd


# =========================================================================================
# OBS-C — force-push target recovery past -o/--push-option (commit 4). The prior naive
# split-filter in _extract_force_push_target_ref counted -o's NON-dash value (`ci.skip`)
# as a 3rd positional -> None -> a faithful force-push carrying a push-option was
# gated-but-unmintable (over-block). The fix reuses the SHARED _push_positionals helper
# (the same one the remote-ref-delete/mass-delete builders use), which skips a value
# flag's value token — recovering the REAL refspec positional. MINT-ENABLING ONLY:
# is_dangerous and the op-class are unchanged; the bound target is the real refspec,
# never the -o value (mint==read via the shared extract_command_context).
# =========================================================================================
_FPUSH = "git " + "push " + "--force "
OBS_C_MINT_RECOVERY = [
    ("o-space", _FPUSH + "-o ci.skip origin main"),
    ("o-space-kv", _FPUSH + "-o ci.skip=true origin main"),
    ("push-option-space", _FPUSH + "--push-option x origin main"),
    ("push-option-inline", _FPUSH + "--push-option=x origin main"),
    ("o-multi", _FPUSH + "-o a -o b origin main"),
    ("o-with-colon-refspec", _FPUSH + "-o x origin feature:main"),
]


class TestObsCForcePushOptionTargetRecovery:
    @pytest.mark.parametrize(
        "label,cmd", OBS_C_MINT_RECOVERY, ids=[r[0] for r in OBS_C_MINT_RECOVERY]
    )
    def test_target_recovered_and_mints(self, label, cmd):
        import merge_guard_pre
        from merge_guard_post import _mint_context_from_bundle, _target_value

        # target recovered = the real refspec positional (never the -o value)
        ctx = mgc.extract_command_context(cmd)
        assert ctx.get("target_ref") == "main", (
            "target_ref not recovered past the push-option value: %r" % cmd
        )
        # full production round-trip: the faithful click MINTS and its token authorizes
        question = {
            "question": "Proceed?",
            "options": [{"label": "Yes", "description": "Run `%s` now" % cmd}],
            "multiSelect": False,
        }
        mint_ctx, refusal = _mint_context_from_bundle([question], {"Proceed?": "Yes"})
        assert mint_ctx is not None, (
            "faithful force-push with a push-option STOPPED MINTING (%s): %r" % (refusal, cmd)
        )
        assert _target_value(mint_ctx) == "main"
        assert merge_guard_pre._token_matches_command({"context": mint_ctx}, cmd) is True

    @pytest.mark.parametrize(
        "label,cmd", OBS_C_MINT_RECOVERY, ids=[r[0] for r in OBS_C_MINT_RECOVERY]
    )
    def test_gating_unchanged(self, label, cmd):
        # MINT-ENABLING ONLY: every faithful force-push still gates at base AND HEAD.
        assert _base()(cmd) is True
        assert D(cmd) is True, "OBS-C changed gating (must only enable the mint): %r" % cmd

    def test_plain_force_push_unchanged(self):
        # no regression on the plain forms: target + gating identical to base.
        base = load_baseline()
        for cmd, want in [
            (_FPUSH + "origin main", "main"),
            (_FPUSH + "origin feature:main", "main"),
            (_FPUSH + "origin", None),   # remote-only: implicit target stays refused
        ]:
            assert mgc.extract_command_context(cmd).get("target_ref") == want, cmd
            assert base.extract_command_context(cmd).get("target_ref") == want, cmd
            assert D(cmd) is True and base.is_dangerous_command(cmd) is True, cmd

    def test_o_value_never_leaks_as_target(self):
        # the #1037 concern under OBS-C: a colon inside a quoted -o value must never be
        # bound as a refspec target — the recovered target is the REAL positional.
        cmd = "git " + "push " + "origin main -o 'ci.message=cleanup :oldref'"
        ctx = mgc.extract_command_context(cmd)
        assert ctx.get("target_ref") == "main"
        assert "oldref" not in str(ctx)


# =========================================================================================
# OBS-E (+ OBS-E/F-PL per-leg) — the UNIFIED refspec-DST push-to-main predicate applied
# BOTH whole-command (via _flag_condition_danger_op) AND PER-LEG (caller-level loops in
# detect_command_operation_type + _stripped_surface_danger). Fixes the inaccurate
# `(?:main|master)(?!:)\b` literal BOTH ways (prefix over-block main-release; <src>:dst
# under-block feature:main) AND restores the ANY-LEG coverage the removed whole-string
# DANGEROUS_PATTERNS rows carried (a push-to-main in a non-first leg, `cd && git push
# origin main`). Executable PL.5 matrix; mint==read symmetry (gate <=> detect non-None).
# =========================================================================================
DETECT = mgc.detect_command_operation_type

# FIX: push-to-main in a NON-first leg now gates (the regression the per-leg loops close).
OBS_E_PERLEG_FIX = [
    ("cd-push-main", "cd /repo && git push origin main"),
    ("cd-lease-main", "cd /repo && git push --force-with-lease origin main"),
    ("fetch-feature-colon-main", "git fetch && git push origin feature:main"),
    ("cd-HEAD-main", "cd /x && git push origin HEAD:main"),
    ("assign-then-push-main", "a=1; git push origin main"),
    ("cd-o-interposed-main", "cd /repo && git push origin -o ci.skip=true main"),
    ("cd-quoted-main", "cd /repo && git push origin 'main'"),
    ("cd-quoted-origin-main", "cd /repo && git push 'origin' \"main\""),
]
# NO-REGRESSION: first-leg push-to-main forms stay gated.
OBS_E_NO_REGRESSION = [
    ("origin-main", "git push origin main"), ("origin-master", "git push origin master"),
    ("HEAD-main", "git push origin HEAD:main"), ("HEAD-master", "git push origin HEAD:master"),
    ("dash-u", "git push -u origin main"), ("set-upstream", "git push --set-upstream origin main"),
    ("non-origin", "git push upstream main"), ("multi-main-master", "git push origin main master"),
    ("lease", "git push --force-with-lease origin main"),
]
# UNDER-BLOCK now gated (the <src>:dst / full-ref spellings the literal missed).
OBS_E_UNDER_BLOCK_NOW_GATE = [
    ("feature-colon-main", "git push origin feature:main"),
    ("develop-colon-main", "git push origin develop:main"),
    ("refs-heads-main", "git push origin refs/heads/main"),
    ("HEAD-refs-heads-main", "git push origin HEAD:refs/heads/main"),
]
# OVER-BLOCK boundary: main-prefixed non-main / remote-named-main / redirect — STAY ungated,
# incl. cd-prefixed (the per-leg loop applies the ACCURATE predicate, not the old prefix `\b`).
OBS_E_OVER_BLOCK_STAYS_UNGATED = [
    ("cd-main-release", "cd /repo && git push origin main-release"),
    ("cd-main-x", "cd /repo && git push origin main-x"),
    ("cd-main-dot-foo", "cd /repo && git push origin main.foo"),
    ("cd-main-at-v1", "cd /repo && git push origin main@v1"),
    ("remote-named-main", "git push main feature"),
    ("cd-remote-named-main", "cd /repo && git push main feature"),
    ("redirect-target-main", "git push origin feature > main"),
    ("cd-redirect-target-main", "cd /repo && git push origin feature > main"),
    ("echo-main-then-push-feature", "echo main && git push origin feature"),
    ("commitmsg-main-then-push-feature", "git commit -m 'see main' && git push origin feature"),
    ("feature-colon-main-release", "git push origin feature:main-release"),
]
# EXCLUDED per-leg residuals (delete/mass/branch-delete in a non-first leg) — the
# {push-to-main, force-push} filter deliberately does NOT gate these (pre-existing, out of
# scope; deleting main is rarely good-faith → acceptable under-block).
OBS_E_PERLEG_RESIDUALS = [
    ("cd-colon-main", "cd /repo && git push origin :main"),
    ("cd-delete-main", "cd /repo && git push origin --delete main"),
    ("fetch-mirror", "git fetch && git push --mirror origin"),
    ("cd-branch-Df", "cd /repo && git branch -Df temp"),
]


class TestObsEPerLegPushToMain:
    @pytest.mark.parametrize("label,cmd", OBS_E_PERLEG_FIX, ids=[r[0] for r in OBS_E_PERLEG_FIX])
    def test_non_first_leg_push_to_main_gates(self, label, cmd):
        assert DETECT(cmd) == "push-to-main"
        assert D(cmd) is True, "non-first-leg push-to-main under-block re-opened: %r" % cmd

    @pytest.mark.parametrize("label,cmd", OBS_E_NO_REGRESSION, ids=[r[0] for r in OBS_E_NO_REGRESSION])
    def test_first_leg_push_to_main_unchanged(self, label, cmd):
        assert DETECT(cmd) == "push-to-main" and D(cmd) is True

    @pytest.mark.parametrize(
        "label,cmd", OBS_E_UNDER_BLOCK_NOW_GATE, ids=[r[0] for r in OBS_E_UNDER_BLOCK_NOW_GATE]
    )
    def test_src_dst_and_full_ref_under_block_now_gates(self, label, cmd):
        assert load_baseline().detect_command_operation_type(cmd) is None, "not an under-block at base"
        assert DETECT(cmd) == "push-to-main" and D(cmd) is True

    @pytest.mark.parametrize(
        "label,cmd", OBS_E_OVER_BLOCK_STAYS_UNGATED, ids=[r[0] for r in OBS_E_OVER_BLOCK_STAYS_UNGATED]
    )
    def test_over_block_boundary_stays_ungated(self, label, cmd):
        # PRIMARY cardinal gate: no faithful non-main / remote-named-main / redirect push gates.
        assert D(cmd) is False, "OBS-E over-blocked a non-main push: %r" % cmd
        assert DETECT(cmd) is None

    @pytest.mark.parametrize(
        "label,cmd", OBS_E_PERLEG_RESIDUALS, ids=[r[0] for r in OBS_E_PERLEG_RESIDUALS]
    )
    def test_excluded_perleg_residuals_stay_ungated(self, label, cmd):
        assert D(cmd) is False, "the {push-to-main,force-push} filter wrongly gated a delete/mass leg"

    def test_precedence_first_leg_unchanged(self):
        assert DETECT("git push origin --delete main") == "remote-ref-delete"
        assert DETECT("git push origin --mirror origin") == "remote-mass-delete"
        assert DETECT("git push --mirror origin main") == "remote-mass-delete"

    def test_cd_prefix_force_and_branch_delete_still_gate(self):
        assert DETECT("cd /repo && git push --for" + "ce origin main") == "force-push"
        assert DETECT("cd /repo && git branch -D main") == "branch-delete"

    def test_mint_read_symmetry_across_matrix(self):
        # gate <=> detect non-None on every matrix row (the mint==read symmetry the shared
        # _PER_LEG_PUSH_OPS filter + one predicate guarantee by construction).
        rows = (OBS_E_PERLEG_FIX + OBS_E_NO_REGRESSION + OBS_E_UNDER_BLOCK_NOW_GATE
                + OBS_E_OVER_BLOCK_STAYS_UNGATED + OBS_E_PERLEG_RESIDUALS)
        for _label, cmd in rows:
            assert (DETECT(cmd) is not None) == (D(cmd) is True), "mint!=read on %r" % cmd


# =========================================================================================
# OBS-F — `+refspec` force-push recognition (commit 2). A leading `+` on any push refspec is
# git's documented FORCE spelling (`git push origin +main` force-updates main), but was
# UNRECOGNIZED for any target (detect=None) while `--force` gated. The +refspec branch in the
# union arm — BETWEEN mass-delete and push-to-main — gates it as force-push; via the OBS-E/F-PL
# per-leg loops it also gates in a NON-first leg. Over-block-safe by construction (force-push
# already gates any target; `+ref` is another spelling of the same op).
# =========================================================================================
_PF2 = "git " + "push "
OBS_F_FORCE_PUSH = [
    ("plus-main", _PF2 + "origin +main"),
    ("plus-master", _PF2 + "origin +master"),
    ("plus-feature", _PF2 + "origin +feature"),
    ("plus-feature-colon-main", _PF2 + "origin +feature:main"),
    ("plus-refs-heads-main", _PF2 + "origin +refs/heads/main"),
    ("cd-plus-main-perleg", "cd /repo && " + _PF2 + "origin +main"),
    ("cd-plus-feature-perleg", "cd /x && " + _PF2 + "origin +feature"),
    ("quoted-plus-notaforce", _PF2 + "origin '+notaforce'"),
]
OBS_F_PRECEDENCE = [
    # +feature main: the + forces → force-push, never push-to-main (a push-to-main token must
    # not authorize a forced ref update).
    ("plus-feature-then-main-force-wins", _PF2 + "origin +feature main", "force-push"),
    # a + on a delete/mass form: delete/mass claim it FIRST (checked above the +refspec branch).
    ("plus-colon-main-delete-wins", _PF2 + "origin +:main", "remote-ref-delete"),
    ("delete-plus-main-delete-wins", _PF2 + "origin --delete +main", "remote-ref-delete"),
    ("mirror-plus-main-mass-wins", _PF2 + "--mirror origin +main", "remote-mass-delete"),
]


class TestObsFPlusRefspecForcePush:
    @pytest.mark.parametrize("label,cmd", OBS_F_FORCE_PUSH, ids=[r[0] for r in OBS_F_FORCE_PUSH])
    def test_plus_refspec_gates_force_push(self, label, cmd):
        assert load_baseline().detect_command_operation_type(cmd) is None, "not ungated at base"
        assert mgc.detect_command_operation_type(cmd) == "force-push"
        assert D(cmd) is True

    @pytest.mark.parametrize("label,cmd,want", OBS_F_PRECEDENCE, ids=[r[0] for r in OBS_F_PRECEDENCE])
    def test_plus_refspec_precedence(self, label, cmd, want):
        assert mgc.detect_command_operation_type(cmd) == want, "precedence wrong: %r" % cmd
        assert D(cmd) is True

    def test_bare_plus_is_not_a_refspec(self):
        # `git push origin +` (bare '+') is not a refspec — the len>1 guard excludes it.
        assert mgc.detect_command_operation_type(_PF2 + "origin +") is None
        assert D(_PF2 + "origin +") is False

    def test_non_plus_forms_unaffected(self):
        assert mgc.detect_command_operation_type(_PF2 + "origin main") == "push-to-main"
        assert mgc.detect_command_operation_type(_PF2 + "origin --delete main") == "remote-ref-delete"
        assert mgc.detect_command_operation_type(_PF2 + "--for" + "ce origin main") == "force-push"
        assert mgc.detect_command_operation_type(_PF2 + "origin feature") is None

    def test_plus_refspec_mint_read_symmetry(self):
        rows = OBS_F_FORCE_PUSH + [(l, c) for l, c, _ in OBS_F_PRECEDENCE]
        for _label, cmd in rows:
            assert (mgc.detect_command_operation_type(cmd) is not None) == (D(cmd) is True), cmd


# =========================================================================================
# OBS-G — multi-ref push-to-main MINTABILITY (the `push_set` distinct key; MINT layer).
# Closes the gated-but-unmintable over-block: post-OBS-E a multi-ref push including main
# (`git push origin main feature`) GATES but the scalar extractor's deliberate `!=2 -> None`
# conservatism yields target=None -> no mint -> the faithful click cannot authorize it. The
# fix transfers the ratified branch_set/mass_target/_canonical_join precedent: one shared
# `_extract_push_to_main_set` (>=2 refspecs -> canonical sort+dedup FULL-refspec netstring
# identity, remote-AGNOSTIC; <2 -> None = the scalar boundary) feeds BOTH the post-hook mint
# (`_target_value` gains push_set) and the pre-hook read (`_token_matches_command`'s
# push-to-main arm binds target_ref OR push_set, set-EQUALITY) -> mint==read by construction.
# SECURITY CERT = the 4+1 property matrix (executable, base-vs-HEAD via the vendored
# fixture, never a byte-diff): (a) injective set-identity over rich refspec tokens;
# (b) SCOPE BOUNDARY — multi-ref force-push/delete stay unmintable
# (`_extract_force_push_target_ref` byte-untouched); (c) scalar<->set exactly-one-populated;
# (d) mint==read round-trip through the REAL hook functions; (e) the REQUIRED 5th
# bound_flags property — a PLAIN push_set token REFUSES a --force-with-lease multi-ref push
# of the same set (the op-agnostic a2 bound_flags equality, checked before the target arms)
# and REFUSES --force multi-ref (op-gate reclassifies force-push). (e) pins behavior the
# code inherits from a2 so a future PRIVILEGED_FLAGS edit cannot silently regress the
# plain<->lease separation.
# =========================================================================================
_PG = "git " + "push "


def PUSH_SET(cmd):
    """Call-time module-attr resolution of the OBS-G-only extractor (revert
    non-vacuity: a source-only revert of OBS-G fails these rows PER-ROW, never as a
    collection-wide ImportError — module-level imports stay limited to stable symbols)."""
    return mgc._extract_push_to_main_set(cmd)


def _gctx(cmd):
    """HEAD command context via the ONE extract_command_context SSOT (both hooks' derivation)."""
    return mgc.extract_command_context(cmd)


def _gtok(cmd):
    """Mint-shaped token: context derived exactly as the post-hook mint derives it."""
    return {"context": _gctx(cmd)}


def _greads(tok, cmd):
    """The REAL pre-hook read predicate (op-gate -> a2 bound_flags -> per-op target arm)."""
    return mgpre._token_matches_command(tok, cmd)


class TestObsGPushSetMintability:
    # ---- the closed over-block (bidirectional vs the vendored base) ----
    def test_base_was_gated_but_unmintable_head_mints(self):
        base = load_baseline()
        cmd = _PG + "origin main feature"
        base_ctx = base.extract_command_context(cmd)
        assert base_ctx.get("operation_type") == "push-to-main", "row not gated at base (vacuous)"
        assert "push_set" not in base_ctx and "target_ref" not in base_ctx, (
            "base minted a multi-ref target — the over-block premise is wrong"
        )
        head_ctx = _gctx(cmd)
        assert head_ctx.get("operation_type") == "push-to-main"
        assert head_ctx.get("push_set") == mgc._canonical_join(["feature", "main"])
        assert mgpost._target_value(head_ctx) == head_ctx["push_set"], "the #1064 mint site misses push_set"

    def test_collect_pairs_mints_exactly_one_pair(self):
        cmd = _PG + "origin main feature"
        pairs = mgpost._collect_pairs([cmd])
        assert list(pairs) == [("push-to-main", mgc._canonical_join(["feature", "main"]))]

    # ---- (a) injective set-identity over rich refspec tokens ----
    def test_identity_canonicalizes_reorder_and_dup(self):
        want = PUSH_SET(_PG + "origin main feature")
        assert want is not None
        assert PUSH_SET(_PG + "origin feature main") == want
        assert PUSH_SET(_PG + "origin main feature feature") == want

    def test_identity_separates_rich_refspec_tokens(self):
        colon = PUSH_SET(_PG + "origin feature:main develop:main")
        assert colon == mgc._canonical_join(["develop:main", "feature:main"])
        # concat-collision refuses: the netstring length-prefix is self-delimiting.
        assert PUSH_SET(_PG + "origin feature:maindevelop:main extra:main") != colon
        assert PUSH_SET(_PG + "origin refs/heads/main feature") == mgc._canonical_join(
            ["feature", "refs/heads/main"]
        )
        # injectivity canaries: a literal comma-name / netstring-shaped name never
        # collides with the set it imitates (the class a bare-comma join failed).
        assert mgc._canonical_join(["a,b"]) != mgc._canonical_join(["a", "b"])
        assert mgc._canonical_join(["4:main"]) != mgc._canonical_join(["main"])

    @pytest.mark.parametrize(
        "label,other",
        [
            ("subset", "origin main"),
            ("superset", "origin main feature staging"),
            ("different-set", "origin main develop"),
        ],
    )
    def test_set_equality_refuses_sub_super_different(self, label, other):
        tok = _gtok(_PG + "origin main feature")
        assert _greads(tok, _PG + other) is False, "%s wrongly authorized" % label

    def test_set_equality_authorizes_same_reorder_dup_and_remote_agnostic(self):
        tok = _gtok(_PG + "origin main feature")
        assert _greads(tok, _PG + "origin main feature") is True
        assert _greads(tok, _PG + "origin feature main") is True
        assert _greads(tok, _PG + "origin main feature feature") is True
        # remote-AGNOSTIC (security-ratified Q5): mirrors the scalar target_ref,
        # which already binds the ref only — no NEW equivalence class.
        assert _greads(tok, _PG + "upstream main feature") is True

    # ---- (b) SCOPE BOUNDARY — force-push / delete multi-ref stay unmintable ----
    @pytest.mark.parametrize(
        "label,cmd,want_op",
        [
            ("force-multi-ref", _PG + "--for" + "ce origin main feature", "force-push"),
            ("plus-multi-ref", _PG + "origin +main +feature", "force-push"),
            ("delete-multi-ref", _PG + "origin :main :feature", "remote-mass-delete"),
        ],
    )
    def test_non_push_to_main_multi_ref_never_gets_push_set(self, label, cmd, want_op):
        ctx = _gctx(cmd)
        assert ctx.get("operation_type") == want_op
        assert "push_set" not in ctx, "the elif scope boundary leaked push_set to %s" % want_op

    def test_multi_ref_force_push_stays_unmintable(self):
        ctx = _gctx(_PG + "--for" + "ce origin main feature")
        assert mgpost._target_value(ctx) is None, "multi-ref force-push became mintable"

    def test_scalar_extractor_multi_ref_behavior_unchanged(self):
        # `_extract_force_push_target_ref` is byte-untouched: multi-ref still None.
        assert mgc._extract_force_push_target_ref(_PG + "--for" + "ce origin main feature") is None
        assert mgc._extract_force_push_target_ref(_PG + "origin main feature") is None

    # ---- (c) scalar <-> set exactly-one-populated ----
    @pytest.mark.parametrize(
        "label,cmd,want_key",
        [
            ("single-ref-scalar", _PG + "origin main", "target_ref"),
            ("single-colon-scalar", _PG + "origin HEAD:main", "target_ref"),
            ("multi-ref-set", _PG + "origin main feature", "push_set"),
            ("multi-colon-set", _PG + "origin feature:main develop", "push_set"),
        ],
    )
    def test_exactly_one_of_target_ref_push_set(self, label, cmd, want_key):
        ctx = _gctx(cmd)
        present = tuple(k for k in ("target_ref", "push_set") if k in ctx)
        assert present == (want_key,), "co-population/wrong key: %r -> %r" % (cmd, present)

    def test_scalar_and_set_tokens_never_cross_authorize(self):
        scalar_tok = _gtok(_PG + "origin main")
        set_tok = _gtok(_PG + "origin main feature")
        assert _greads(scalar_tok, _PG + "origin main feature") is False
        assert _greads(set_tok, _PG + "origin main") is False
        assert _greads(scalar_tok, _PG + "origin main") is True  # scalar path unchanged

    # ---- (d) mint==read round-trip through the REAL hook functions ----
    def test_mint_read_round_trip(self):
        cmd = _PG + "origin main feature"
        ctx = _gctx(cmd)
        minted = mgpost._target_value(ctx)
        assert minted is not None
        # the read side re-derives the byte-identical identity from the SAME extractor
        assert _greads({"context": ctx}, cmd) is True
        assert ctx["push_set"] == PUSH_SET(cmd) == minted

    def test_mint_subset_of_read_over_matrix(self):
        # mint⊆read: any command that mints a push_set is is_dangerous (gated) too.
        for cmd in [
            _PG + "origin main feature",
            _PG + "origin feature main",
            _PG + "upstream main feature",
            _PG + "origin -o ci.skip=true main feature",
            _PG + "origin feature:main develop",
        ]:
            if PUSH_SET(cmd) is not None and _gctx(cmd).get("push_set"):
                assert D(cmd) is True, "minted but not gated (mint⊄read): %r" % cmd

    # ---- (e) the REQUIRED 5th property: bound_flags negatives (a2 inheritance) ----
    def test_plain_set_token_refuses_lease_multi_ref(self):
        plain = _gtok(_PG + "origin main feature")
        assert plain["context"].get("bound_flags") == []
        lease_cmd = _PG + "--for" + "ce-with-lease origin main feature"
        # premise: the lease multi-ref classifies push-to-main with lease BOUND,
        # so the a2 set-equality (plain [] vs [--force-with-lease]) refuses.
        lease_ctx = _gctx(lease_cmd)
        assert lease_ctx.get("operation_type") == "push-to-main"
        assert lease_ctx.get("bound_flags") == ["--for" + "ce-with-lease"]
        assert _greads(plain, lease_cmd) is False, (
            "a PLAIN push_set token authorized a history-rewriting lease push"
        )

    def test_plain_set_token_refuses_force_multi_ref(self):
        plain = _gtok(_PG + "origin main feature")
        force_cmd = _PG + "--for" + "ce origin main feature"
        # premise: --force multi-ref reclassifies force-push -> the op-gate refuses.
        assert _gctx(force_cmd).get("operation_type") == "force-push"
        assert _greads(plain, force_cmd) is False

    def test_lease_set_token_binds_symmetrically(self):
        lease_cmd = _PG + "--for" + "ce-with-lease origin main feature"
        lease_tok = _gtok(lease_cmd)
        assert _greads(lease_tok, lease_cmd) is True  # faithful lease click round-trips
        # never-escalate is symmetric-REFUSE: the lease token does not authorize the
        # plain command either (any bound_flags difference refuses).
        assert _greads(lease_tok, _PG + "origin main feature") is False

    # ---- seam: cd-prefix / compound — the set INHERITS the scalar posture ----
    def test_cd_prefix_whole_command_extract_stays_unmintable(self):
        # the whole-command extract is first-leg-anchored -> target None (OBS-H scope);
        # SYMMETRIC for scalar and set: neither key populates, detect still gates.
        for cmd in ["cd /repo && " + _PG + "origin main", "cd /repo && " + _PG + "origin main feature"]:
            ctx = _gctx(cmd)
            assert ctx.get("operation_type") == "push-to-main"
            assert "target_ref" not in ctx and "push_set" not in ctx
            assert mgpost._target_value(ctx) is None

    def test_cd_prefix_quoted_presentation_symmetrically_mintable(self):
        # CONTROL-SWAP (OBS-H): the quoted compound region now extracts through the
        # read-symmetric _extraction_surface -> the destructive LEG -> a pair IS
        # collected, identically for scalar and set (previously symmetric-unmintable;
        # the intended posture CHANGED with the extraction-surface fix, so this row
        # flipped as a control-swap, not a reframe).
        scalar = mgpost._collect_pairs(["Approve: `cd /repo && " + _PG + "origin main`"])
        multi = mgpost._collect_pairs(["Approve: `cd /repo && " + _PG + "origin main feature`"])
        assert list(scalar) == [("push-to-main", "main")]
        assert list(multi) == [("push-to-main", mgc._canonical_join(["feature", "main"]))]

    def test_cd_prefix_bare_presentation_set_mirrors_scalar(self):
        # bare-text presentation: locate_command_regions recovers the `git ...` span for
        # BOTH (pre-existing scalar behavior); the set inherits the identical shape —
        # no scalar/set divergence, no NEW seam opened by the push_set key.
        scalar = mgpost._collect_pairs(["cd /repo && " + _PG + "origin main"])
        multi = mgpost._collect_pairs(["cd /repo && " + _PG + "origin main feature"])
        assert (len(scalar) > 0) == (len(multi) > 0)

    # ---- fail-safe residuals: no canonical identity -> None (never over-broad) ----
    @pytest.mark.parametrize(
        "label,cmd",
        [
            ("unbalanced-quote", _PG + "origin main 'feature"),
            ("procsub", _PG + "origin main <(echo feature)"),
            ("flag-flood", _PG + " ".join("-x%d" % i for i in range(40)) + " origin main feature"),
            ("zero-ref", _PG + "origin"),
            ("single-ref-boundary", _PG + "origin main"),
            ("glued-non-command", "git " + "push--for" + "ce origin main feature"),
        ],
    )
    def test_fail_safe_shapes_yield_none(self, label, cmd):
        assert PUSH_SET(cmd) is None, "%s minted an over-broad set identity" % label


# =========================================================================================
# OBS-G — REAL mint -> execute round-trip (the cert BACKBONE, the sibling of the #1129
# harness). Drives the ACTUAL post_main (AskUserQuestion approval whose clicked option
# embeds the command in backticks -> token mint) then the ACTUAL pre_main (Bash exec ->
# allow/deny) against the same token dir. This layer is MANDATORY for a mint-layer fix:
# the recorded #1129 miss (branch_set registered at extract+read but MISSING from
# _target_value) was invisible to every test that hand-constructs token context — a
# faithful click minted ZERO tokens while all read-side units stayed green. minted==1 is
# asserted FIRST on every refusal row so a DENY is proven a READ decision, never a
# mint-side miss masquerading as one. The three sites are independently load-bearing
# through these rows: EXTRACT (exactly-one-populated above), MINT (minted==1 — a
# _target_value miss goes red here), READ (exec-same ALLOW — a read-arm miss goes red).
# =========================================================================================
_G_ALLOW, _G_DENY = 0, 2


def _g_mint(cmd, tok):
    """Drive the REAL post hook with an approval embedding `cmd`; return the count of
    tokens minted by this call."""
    before = set(tok.glob("merge-authorized-*"))
    env = json.dumps({
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{
            "question": "Proceed?",
            "options": [
                {"label": "Yes", "description": "Run `%s`" % cmd},
                {"label": "Cancel", "description": "Abort"},
            ],
        }]},
        "tool_response": {"answers": {"Proceed?": "Yes"}},
        "session_id": "obs-g-cert",
    })
    with patch.object(mgpost, "TOKEN_DIR", tok), \
            patch("sys.stdin", io.StringIO(env)), \
            patch("sys.stdout", io.StringIO()):
        try:
            post_main()
        except SystemExit as e:
            assert e.code == 0, "post hook exited nonzero: %r" % (e.code,)
    return len(set(tok.glob("merge-authorized-*")) - before)


def _g_execute(cmd, tok):
    """Run `cmd` through the REAL pre hook; return exit code (0=ALLOW, 2=DENY)."""
    env = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "session_id": "obs-g-cert",
    })
    with patch.object(mgpre, "TOKEN_DIR", tok), \
            patch("sys.stdin", io.StringIO(env)), \
            patch("sys.stdout", io.StringIO()), \
            patch("sys.stderr", io.StringIO()):
        try:
            pre_main()
            return 0
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 0


def _g_roundtrip(mint_cmd, exec_cmd, tok):
    return _g_mint(mint_cmd, tok), _g_execute(exec_cmd, tok)


class TestObsGRealMintExecuteRoundTrip:
    def test_faithful_multi_ref_click_mints_and_self_authorizes(self, tmp_path):
        # THE over-block cure, end-to-end: the faithful multi-ref click MINTS (base
        # minted zero — gated-but-unmintable) and its own execution is ALLOWED.
        cmd = _PG + "origin main feature"
        minted, rc = _g_roundtrip(cmd, cmd, tmp_path)
        assert minted == 1, "faithful multi-ref click did not mint (the over-block persists)"
        assert rc == _G_ALLOW

    def test_reorder_and_dup_authorize(self, tmp_path):
        minted, rc = _g_roundtrip(_PG + "origin main feature", _PG + "origin feature main", tmp_path)
        assert minted == 1 and rc == _G_ALLOW
        minted, rc = _g_roundtrip(_PG + "origin main feature",
                                  _PG + "origin main feature feature", tmp_path)
        assert minted == 1 and rc == _G_ALLOW

    @pytest.mark.parametrize(
        "label,exec_tail",
        [
            ("superset", "origin main feature staging"),
            ("subset-scalar", "origin main"),
            ("different-set", "origin main develop"),
        ],
    )
    def test_set_equality_denies_other_sets(self, label, exec_tail, tmp_path):
        minted, rc = _g_roundtrip(_PG + "origin main feature", _PG + exec_tail, tmp_path)
        assert minted == 1, "the {main,feature} approval must MINT (refuse must be a read decision)"
        assert rc == _G_DENY, "a {main,feature} token wrongly authorized the %s" % label

    def test_scalar_token_denies_set_execution(self, tmp_path):
        minted, rc = _g_roundtrip(_PG + "origin main", _PG + "origin main feature", tmp_path)
        assert minted == 1, "the scalar approval must still MINT (pre-existing path)"
        assert rc == _G_DENY, "a scalar main token wrongly authorized a multi-ref push"

    def test_plain_set_token_denies_lease_and_force_execution(self, tmp_path):
        # the 5th property END-TO-END: a PLAIN multi-ref token refuses the
        # history-rewriting spellings of the same set (a2 bound_flags / op-gate).
        minted, rc = _g_roundtrip(_PG + "origin main feature",
                                  _PG + "--for" + "ce-with-lease origin main feature", tmp_path)
        assert minted == 1
        assert rc == _G_DENY, "plain push_set token authorized a lease multi-ref push"
        minted, rc = _g_roundtrip(_PG + "origin main feature",
                                  _PG + "--for" + "ce origin main feature", tmp_path)
        assert minted == 1
        assert rc == _G_DENY, "plain push_set token authorized a --force multi-ref push"

    def test_multi_ref_force_push_approval_stays_unmintable(self, tmp_path):
        # scope boundary end-to-end: the multi-ref FORCE approval mints ZERO tokens
        # (its relaxation is out of scope) and the execution stays denied.
        cmd = _PG + "--for" + "ce origin main feature"
        minted, rc = _g_roundtrip(cmd, cmd, tmp_path)
        assert minted == 0, "a multi-ref force-push approval minted — scope boundary broken"
        assert rc == _G_DENY

    def test_cd_prefix_compound_approval_symmetrically_mintable_end_to_end(self, tmp_path):
        # CONTROL-SWAP (OBS-H): the cd-prefix seam through the REAL pipeline — the
        # quoted compound approval now extracts through _extraction_surface -> the
        # destructive leg -> MINTS, and the (unchanged) read side tier-1-binds the
        # compound execution -> ALLOW. Previously pinned symmetric-unmintable; the
        # intended posture changed with the extraction-surface fix (control-swap).
        cmd = "cd /repo && " + _PG + "origin main feature"
        minted, rc = _g_roundtrip(cmd, cmd, tmp_path)
        assert minted == 1, "the faithful cd-prefix multi-ref click did not mint"
        assert rc == _G_ALLOW

    @pytest.mark.parametrize(
        "label,approve_tail,exec_tail",
        [
            ("compound-approve-compound-exec", "cd /repo && {push}origin {refs}", "cd /repo && {push}origin {refs}"),
            ("cleanleg-approve-compound-exec", "{push}origin {refs}", "cd /repo && {push}origin {refs}"),
            ("compound-approve-cleanleg-exec", "cd /repo && {push}origin {refs}", "{push}origin {refs}"),
        ],
    )
    def test_cd_prefix_posture_set_inherits_scalar(self, label, approve_tail, exec_tail, tmp_path):
        # NO-NEW-SEAM guarantee: on every cd-prefix shape the SET's real end-to-end
        # outcome (did it mint, allow/deny) equals the SCALAR's pre-existing outcome.
        # Deliberately pinned as INHERITANCE (set == scalar), not as a normative
        # absolute, so a future uniform compound-posture change moves both together
        # without falsifying this cert.
        scalar_dir = tmp_path / "scalar"
        set_dir = tmp_path / "set"
        scalar_dir.mkdir()
        set_dir.mkdir()
        s_m, s_rc = _g_roundtrip(
            approve_tail.format(push=_PG, refs="main"),
            exec_tail.format(push=_PG, refs="main"), scalar_dir)
        t_m, t_rc = _g_roundtrip(
            approve_tail.format(push=_PG, refs="main feature"),
            exec_tail.format(push=_PG, refs="main feature"), set_dir)
        assert (s_m > 0) == (t_m > 0) and s_rc == t_rc, (
            "set diverged from the scalar posture on %s: scalar=%s/%s set=%s/%s"
            % (label, s_m, s_rc, t_m, t_rc)
        )


# =========================================================================================
# OBS-H — cd-prefix per-leg EXTRACTION mintability (the `_extraction_surface` fix; MINT
# surface, the arc's last cure). Closes the last over-block: a QUOTED single-destructive
# cd-prefix compound approval (`Approve: \`cd /repo && git push origin main\``) detected
# push-to-main (whole-candidate, per-leg detection) but extracted first-leg-anchored (the
# cd leg -> no target) -> no pair -> gated-but-unmintable. The fix: extraction at the TWO
# mint call sites consumes `_extraction_surface(region)` — the read side's VERBATIM
# three-tier expression (tier-1 unique dangerous leg; tier-2 unique detectable leg; else
# the whole region = the fail-safe unmintable collapse). SECURITY = the REDUCTION THEOREM
# (ratified #89/#91): mint(quoted cd-prefix X) == mint(bare leg-of-X on already-certified
# bytes) by dict-equality on the FULL (op, target, bound_flags); the read side is
# byte-untouched, so every post-fix outcome maps to an already-certified pre-fix outcome.
# REQUIRED addition (the #91 RATIFY-WITH-CHANGES condition): NON-PUSH-class cd-prefix
# quoted mint PARITY pins — the surface rewires extraction for ALL op-classes, so the
# merge/close/branch-delete/ref-delete parity is pinned against a future helper/tier-fn
# edit silently regressing a non-push class (mirrors the OBS-G 5th-property pin).
# =========================================================================================
def _h_mint(desc):
    """Context-level mint through the REAL `_mint_context_from_bundle` (the mint entry
    the post hook runs after option selection): returns (context, refusal_reason)."""
    q = {"question": "Proceed?",
         "options": [{"label": "Yes", "description": desc}],
         "multiSelect": False}
    return mgpost._mint_context_from_bundle([q], {"Proceed?": "Yes"})


def _h_reads(ctx, cmd):
    return mgpre._token_matches_command({"context": ctx}, cmd)


_H_CMD = "cd /repo && " + _PG + "origin main"
_H_LEG = _PG + "origin main"
_H_CMDS = "cd /repo && " + _PG + "origin main feature"
_H_LEGS = _PG + "origin main feature"
_H_CMDF = "cd /repo && " + _PG + "--for" + "ce origin main"
_H_LEGF = _PG + "--for" + "ce origin main"
_H_CMDL = "cd /repo && " + _PG + "--for" + "ce-with-lease origin main"
_H_LEGL = _PG + "--for" + "ce-with-lease origin main"
_H_MERGE = "gh " + "pr " + "merge"
_H_CLOSE = "gh " + "pr " + "close"


class TestObsHExtractionSurfaceMintability:
    def test_surface_strip_free_identity_and_value_carrying_context_equality(self):
        # The reduction theorem's premise, STRIP-AWARE: for a STRIP-FREE bare
        # single-command region the surface is the region itself (tier-1 == region,
        # text identity). For a VALUE-CARRYING command, tier-1 returns the
        # inert-value-STRIPPED leg — text identity does NOT hold; the load-bearing
        # invariant is context-EQUALITY: the real extractor derives the SAME
        # (op, target, bound_flags) from the stripped surface as from the raw region.
        # (The read side has derived from this stripped view all along — the
        # extraction-surface fix ALIGNS the mint with it; a raw-vs-stripped context
        # divergence on a real shape would have been a mint/read disagreement
        # already, so this row is the tripwire for that class.)
        for leg in (_H_LEG, _H_LEGS, "gh " + "pr " + "merge 5", "git branch -Df stale"):
            assert mgpost._extraction_surface(leg) == leg
        for cmd in (
            "gh api -X PUT -f note=pulls/5/merge repos/o/r/pulls/6/merge",
            _H_MERGE + ' 6 --body "see pulls/5/merge"',
            _PG + 'origin main -o ci.message="push origin master"',
            _H_CLOSE + ' 9 -c "do not close 7"',
        ):
            surf = mgpost._extraction_surface(cmd)
            assert mgc.extract_command_context(surf) == mgc.extract_command_context(cmd), (
                "raw-vs-stripped context divergence on %r (surface=%r)" % (cmd, surf)
            )

    @pytest.mark.parametrize(
        "label,compound,leg,want_key,want_val",
        [
            ("scalar", _H_CMD, _H_LEG, "target_ref", "main"),
            ("set", _H_CMDS, _H_LEGS, "push_set", None),  # None -> computed canonical below
            ("force", _H_CMDF, _H_LEGF, "target_ref", "main"),
            ("lease", _H_CMDL, _H_LEGL, "target_ref", "main"),
        ],
    )
    def test_fix_token_space_and_read_round_trip(self, label, compound, leg, want_key, want_val):
        if want_val is None:
            want_val = mgc._canonical_join(["feature", "main"])
        ctx, refusal = _h_mint("Run `%s` now" % compound)
        assert ctx is not None, "quoted cd-prefix %s did not mint: %r" % (label, refusal)
        assert ctx.get(want_key) == want_val
        # THE THEOREM: dict-equality with the bare-leg mint on the same bytes.
        bare, _ = _h_mint("Run: " + leg)
        assert ctx == bare, "token-space changed: compound=%r bare=%r" % (ctx, bare)
        # round-trip through the REAL (byte-untouched) read side.
        assert _h_reads(ctx, compound) is True
        assert _h_reads(ctx, leg) is True

    @pytest.mark.parametrize(
        "label,exec_cmd",
        [
            ("scalar-to-set", _H_CMDS),
            ("target-swap", "cd /repo && " + _PG + "origin feature"),
            ("op-escalation-force", _H_CMDF),
            ("flag-escalation-lease", _H_CMDL),
        ],
    )
    def test_deny_negatives_on_the_scalar_compound_token(self, label, exec_cmd):
        ctx, _ = _h_mint("Run `%s` now" % _H_CMD)
        assert ctx is not None
        assert _h_reads(ctx, exec_cmd) is False, "%s wrongly authorized" % label

    def test_deny_negatives_on_the_set_compound_token(self):
        ctx, _ = _h_mint("Run `%s` now" % _H_CMDS)
        assert ctx is not None
        assert _h_reads(ctx, "cd /repo && " + _PG + "origin main feature staging") is False
        assert _h_reads(ctx, _H_CMD) is False

    @pytest.mark.parametrize(
        "label,desc,want_key,want_val",
        [
            ("simple-quoted", "Run `" + _PG + "origin main` now", "target_ref", "main"),
            ("simple-bare", "Run: " + _PG + "origin main", "target_ref", "main"),
            ("merge-compound-quoted", "Run `cd /x && " + _H_MERGE + " 5` now", "pr_number", "5"),
            ("merge-neighbor-quoted", "Run `" + _H_MERGE + " 5 && echo done` now", "pr_number", "5"),
            ("first-leg-delete-quoted",
             "Run `" + _PG + "origin --delete stale && make deploy` now", "target_ref", "stale"),
            # the two case-(ii) rows (quoted UNDETECTED compounds already leg-recover via
            # the bare-subspan path — pre-existing; must keep minting identically):
            ("case-ii-colon-refspec", "Run `cd /repo && " + _PG + "origin :main` now",
             "target_ref", "main"),
            ("case-ii-cluster-delete", "Run `cd /repo && git branch -Df temp` now",
             "branch", "temp"),
        ],
    )
    def test_noreg_today_minting_shapes_keep_minting(self, label, desc, want_key, want_val):
        ctx, refusal = _h_mint(desc)
        assert ctx is not None and ctx.get(want_key) == want_val, (
            "NOREG %s: ctx=%r refusal=%r" % (label, ctx, refusal)
        )

    # ---- the #91 REQUIRED addition: NON-PUSH-class cd-prefix quoted mint parity ----
    @pytest.mark.parametrize(
        "label,compound,leg,want_key,want_val",
        [
            ("merge", "cd /x && " + _H_MERGE + " 7 --admin",
             _H_MERGE + " 7 --admin", "pr_number", "7"),
            ("close", "cd /x && " + _H_CLOSE + " 9 --delete-branch",
             _H_CLOSE + " 9 --delete-branch", "pr_number", "9"),
            ("branch-delete", "cd /x && git branch -D hotfix",
             "git branch -D hotfix", "branch", "hotfix"),
            ("ref-delete", "cd /x && " + _PG + "origin --delete stale",
             _PG + "origin --delete stale", "target_ref", "stale"),
        ],
    )
    def test_non_push_class_cd_prefix_quoted_mint_parity(self, label, compound, leg,
                                                         want_key, want_val):
        # `_extraction_surface` rewires extraction for ALL op-classes at the two mint
        # sites; this pins the non-push classes' parity so a future edit to the helper
        # or the tier functions cannot silently regress one of them.
        ctx, refusal = _h_mint("Run `%s` now" % compound)
        bare, _ = _h_mint("Run: " + leg)
        assert ctx is not None, "PARITY %s: quoted cd-prefix did not mint: %r" % (label, refusal)
        assert ctx.get(want_key) == want_val and ctx == bare, (
            "PARITY %s broke: ctx=%r bare=%r" % (label, ctx, bare)
        )

    # ---- the H.10 wrapped-merge mint-parity discriminators ----
    @pytest.mark.parametrize(
        "label,desc",
        [
            ("bash-c-wrapped", "Run `bash -c '" + _H_MERGE + " 5'` now"),
            ("sh-c-wrapped", 'Run `sh -c "' + _H_MERGE + ' 5"` now'),
        ],
    )
    def test_wrapped_merge_keeps_minting(self, label, desc):
        # tier-1 of a single-command region is the region itself, so the quoted-region
        # wrapped spellings are NOREG by construction — pinned as standing discriminators.
        ctx, refusal = _h_mint(desc)
        assert ctx is not None and ctx.get("pr_number") == "5", (
            "wrapped %s stopped minting: %r" % (label, refusal)
        )

    # ---- structural couplings, pinned executably ----
    def test_gap5_refuses_chimeric_before_pair_collection(self):
        # refusal_reason MUST be compound_command (GAP5), not multiple_commands or
        # no_command — proving the compound refuse still fires BEFORE pair collection
        # with the extraction-surface rewire in place.
        _, refusal = _h_mint("Run `" + _H_MERGE + " 5 && " + _PG + "--for" + "ce origin main` now")
        assert refusal == "compound_command"

    def test_multiplicity_still_refuses_two_commands(self):
        _, refusal = _h_mint("Run `" + _PG + "origin main` or `" + _H_MERGE + " 7` now")
        assert refusal == "multiple_commands"

    def test_three_leg_single_destructive_mints(self):
        ctx, _ = _h_mint("Run `cd /a && cd /b && " + _PG + "origin main` now")
        assert ctx is not None and ctx.get("target_ref") == "main"

    def test_upstream_compound_gate_both_directions(self):
        assert mgc.is_compound_destructive_command(
            _PG + "origin main && " + _PG + "--for" + "ce origin f") is True
        assert mgc.is_compound_destructive_command(_H_CMD) is False

    def test_per_leg_detect_to_tier1_coupling(self):
        # THE COUPLING (lead ruling): the cd-prefix mint depends on (a) per-leg
        # detection covering the whole compound as a region (push classes via the
        # per-leg filter) AND (b) tier-1 selecting the destructive leg as the
        # extraction surface. If the per-leg filter shrinks, the mint regresses to
        # unmintable (fail-safe) — but THIS row goes RED with a named diagnosis
        # instead of a silent posture change.
        assert mgc.detect_command_operation_type(_H_CMD) == "push-to-main"
        assert mgpost._extraction_surface(_H_CMD) == _H_LEG

    def test_ambiguity_collapses_to_whole_region_fail_safe(self):
        # 2 destructive legs -> tier-1 None; 2 detectable legs -> tier-2 None; the
        # surface collapses to the WHOLE region = first-leg-anchored = unmintable
        # (the over-block direction, never an over-broad token). GAP5 independently
        # refuses this shape at the bundle level (pinned above).
        two = _PG + "origin main && " + _PG + "--for" + "ce origin f"
        assert mgpost._extraction_surface(two) == two


class TestObsHRealMintExecuteRetireRoundTrip:
    # The full-pipeline layer (real post_main -> pre_main -> _retire_token_for_command),
    # reusing the OBS-G harness. minted==1 asserted first on every DENY row.
    def test_quoted_cd_prefix_scalar_mints_and_self_authorizes(self, tmp_path):
        minted, rc = _g_roundtrip(_H_CMD, _H_CMD, tmp_path)
        assert minted == 1, "the faithful quoted cd-prefix click did not mint"
        assert rc == _G_ALLOW

    def test_compound_token_authorizes_bare_leg_and_vice_versa(self, tmp_path):
        minted, rc = _g_roundtrip(_H_CMD, _H_LEG, tmp_path)
        assert minted == 1 and rc == _G_ALLOW
        minted, rc = _g_roundtrip(_H_LEG, _H_CMD, tmp_path)
        assert minted == 1 and rc == _G_ALLOW

    def test_deny_rows_minted_first(self, tmp_path):
        minted, rc = _g_roundtrip(_H_CMD, _H_CMDS, tmp_path)
        assert minted == 1, "refusal must be a read decision, not a mint miss"
        assert rc == _G_DENY
        minted, rc = _g_roundtrip(_H_CMD, _H_CMDF, tmp_path)
        assert minted == 1
        assert rc == _G_DENY

    def test_retirement_round_trip_wrong_target_first(self, tmp_path):
        # mint from the quoted compound approval; the token REFUSES retirement against
        # a wrong-target compound (survives), then RETIRES on its own compound
        # execution — the 4th consumer (byte-unchanged) selects the same leg the
        # mint bound.
        def live():
            return [p for p in tmp_path.glob("merge-authorized-*")
                    if not p.name.endswith(".consumed") and ".use-" not in p.name]
        minted = _g_mint(_H_CMDS, tmp_path)
        assert minted == 1
        wrong = mgpost._retire_token_for_command(
            "cd /repo && " + _PG + "origin main develop", "push-to-main", tmp_path)
        assert wrong is False and len(live()) == 1, "wrong-target retirement consumed the token"
        right = mgpost._retire_token_for_command(_H_CMDS, "push-to-main", tmp_path)
        assert right is True and len(live()) == 0, "the token did not retire on its own compound"
