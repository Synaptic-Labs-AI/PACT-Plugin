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
