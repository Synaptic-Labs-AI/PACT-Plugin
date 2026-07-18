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
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

import shared.merge_guard_common as mgc  # noqa: E402
from merge_guard_baseline_loader import load_baseline  # noqa: E402

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
