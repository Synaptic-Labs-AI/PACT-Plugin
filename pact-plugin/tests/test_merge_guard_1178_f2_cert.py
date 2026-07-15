"""
Location: pact-plugin/tests/test_merge_guard_1178_f2_cert.py
Summary: Durable BIDIRECTIONAL companion cert for the F2 over-block closure (commit 6730f908),
         the peer-review-phase remediation stacked on the #1178 fix. F2 = the pre-existing
         COARSE-wrapper WRAPPED-INERT over-block: at base 89061755, busybox and stdbuf were
         _EXEC_WRAPPERS_COARSE, so a faithful inert click behind them (`busybox mycmd "…git
         branch -D…"`, `stdbuf -oL mycmd "…"`) preserved the WHOLE wrapped command -> over-block
         (cardinal sin). The fix: busybox+stdbuf COARSE->RECURSE (find/flock STAY COARSE per the
         #1098 unbounded-grammar wall); hush/lash/msh added to _SHELL_STRING_EXECUTORS; busybox
         empty-grammar + stdbuf value-flag _WrapperGrammar; a GENERAL attached-short-value walker
         branch (`-oL` = short value-flag `-o` + glued value `L`, never bundled bools).

         Certifies against the REAL is_dangerous_command, base(89061755)-vs-HEAD(6730f908). Baked
         baseline via git show (crash-atomic; the code is committed, so code-then-tests ordering
         is satisfied). The 184-row test_merge_guard_1178_cert.py already covers c21eae19->#1178;
         this is its F2 companion. Destructive verbs assembled at runtime (BD/PF) — file stays
         inert to the live guard.

         MONOTONICITY IS SCOPED (the honest-cert crux). backend-r2's fix has two parts with
         DIFFERENT monotonicity behavior:
           (1) busybox/stdbuf COARSE->RECURSE + the attached-short-value walker are STRICTLY
               monotonic-toward-False (they only close over-blocks) -> TestF2MonotonicityFaithfulInert
               asserts NO base-False->HEAD-True for these rows.
           (2) Adding hush/lash/msh to _SHELL_STRING_EXECUTORS CORRECTLY produces base-False->HEAD-True
               for head-basename in {hush,lash,msh} -> pinned EXPLICITLY as intended in
               TestF2StandaloneShellUnderBlockFix (an under-block FIX, not a new over-block).
         A blanket "0 base-False->HEAD-True" sweep would FALSELY red on (2). The property that
         matters — no FAITHFUL INERT click newly blocked — is carried by the CLOSURE rows
         (busybox/stdbuf/walker mycmd forms all base-True->HEAD-False = freed, never newly blocked).

         NON-VACUITY: every closure row asserts D_BASE(89061755) is True (genuinely over-blocked
         at base) AND D is False; every retention asserts D_BASE True AND D True; the standalone
         rows assert D_BASE False AND D True (the under-block that the fix closes). So the cert
         FAILS wholesale against base 89061755 (>=1 row proof, mandated). Plus a mutant-of-live-
         source proof that busybox RECURSE membership is load-bearing for the retention.
"""
import subprocess
import sys
import types
from pathlib import Path

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import shared.merge_guard_common as mgc  # noqa: E402

D = mgc.is_dangerous_command          # PATCH = live worktree HEAD (6730f908, F2 fix)

_BASE_SHA = "89061755"  # pre-F2 (busybox/stdbuf COARSE; hush/lash/msh unrecognized) = 6730f908^


def _load_classifier(sha):
    """Load merge_guard_common as it existed at `sha`, or None if unavailable (shallow clone)."""
    wt = Path(__file__).resolve().parents[2]
    try:
        src = subprocess.check_output(
            ["git", "-C", str(wt), "show",
             sha + ":pact-plugin/hooks/shared/merge_guard_common.py"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    mod = types.ModuleType("merge_guard_common_f2_" + sha)
    mod.__file__ = str(wt / "pact-plugin/hooks/shared/merge_guard_common.py")
    mod.__package__ = "shared"
    try:
        exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    except Exception:
        return None
    return mod


_BASE = _load_classifier(_BASE_SHA)
D_BASE = _BASE.is_dangerous_command if _BASE is not None else None

requires_history = pytest.mark.skipif(
    _BASE is None,
    reason="base(89061755)-vs-HEAD differential requires merged history (shallow clone)",
)

BD = "git " + "branch " + "-D victim"           # danger inside the quoted arg
PF = "git " + "push " + "--force origin main"


def _closure(cmd):
    assert D_BASE(cmd) is True, "vacuous: not over-blocked at base 89061755: %r" % (cmd,)
    assert D(cmd) is False, "F2 over-block NOT closed at HEAD: %r" % (cmd,)


def _retention(cmd):
    assert D_BASE(cmd) is True, "vacuous: not caught at base 89061755: %r" % (cmd,)
    assert D(cmd) is True, "UNDER-BLOCK opened at HEAD (executor freed): %r" % (cmd,)


def _standalone_underblock_fix(cmd):
    # INTENDED base-False -> HEAD-True: a pre-existing standalone under-block the F2 change
    # closes by adding hush/lash/msh to _SHELL_STRING_EXECUTORS.
    assert D_BASE(cmd) is False, "expected base-False (the pre-fix under-block): %r" % (cmd,)
    assert D(cmd) is True, "standalone-shell under-block NOT closed at HEAD: %r" % (cmd,)


# =========================================================================================
# CLASS A — F2 OVER-BLOCK CLOSURES (base-True -> HEAD-False). The primary gate: a faithful
# inert click behind busybox/stdbuf/attached-short-value-walker frees. Danger INSIDE the arg.
# =========================================================================================
class TestF2Closures:
    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        ("busybox mycmd",           'busybox mycmd "%s"' % BD),
        ("stdbuf -oL mycmd",        'stdbuf -oL mycmd "%s"' % BD),
        ("stdbuf -o L mycmd",       'stdbuf -o L mycmd "%s"' % BD),
        ("stdbuf --output=L mycmd", 'stdbuf --output=L mycmd "%s"' % BD),
        ("stdbuf -eL mycmd",        'stdbuf -eL mycmd "%s"' % BD),
        ("stdbuf -e0 -oL mycmd",    'stdbuf -e0 -oL mycmd "%s"' % BD),
        ("stdbuf -o 0 mycmd (sep val)", 'stdbuf -o 0 mycmd "%s"' % BD),
        # NEW general attached-short-value walker on the existing RECURSE wrappers:
        ("timeout -s9 mycmd",       'timeout -s9 mycmd "%s"' % BD),
        ("nice -n5 mycmd",          'nice -n5 mycmd "%s"' % BD),
        ("ionice -c2 mycmd",        'ionice -c2 mycmd "%s"' % BD),
        ("nocache -n3 mycmd",       'nocache -n3 mycmd "%s"' % BD),
        ("torsocks -d5 mycmd",      'torsocks -d5 mycmd "%s"' % BD),
        ("xargs -n1 mycmd",         'xargs -n1 mycmd "%s"' % BD),
        ("sudo -uroot mycmd",       'sudo -uroot mycmd "%s"' % BD),
        ("rlwrap -Oregexp mycmd",   'rlwrap -Oregexp mycmd "%s"' % BD),
        # compound leg-locality: the inert busybox leg frees while the compound survives.
        ("compound inert busybox free leg", 'echo ok && busybox mycmd "%s"' % BD),
    ])
    def test_closure(self, label, cmd):
        _closure(cmd)


# =========================================================================================
# CLASS B — F2 UNDER-BLOCK RETENTIONS (base-True -> HEAD-True). The recursion MUST reach the
# nested executor. These are the pins the monotonicity sweep is BLIND to (base-True->HEAD-True),
# so they are the LOAD-BEARING guard against a fix-introduced under-block. Danger INSIDE the arg.
# =========================================================================================
class TestF2Retentions:
    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        # busybox applet retentions (the applet name is token[1]; each must be a recognized exec).
        ("busybox sh -c",   'busybox sh -c "%s"' % BD),
        ("busybox ash -c",  'busybox ash -c "%s"' % BD),
        ("busybox bash -c", 'busybox bash -c "%s"' % BD),
        ("busybox hush -c", 'busybox hush -c "%s"' % BD),   # hush/lash/msh recognition is load-bearing
        ("busybox lash -c", 'busybox lash -c "%s"' % BD),
        ("busybox msh -c",  'busybox msh -c "%s"' % BD),
        ("busybox dash -c", 'busybox dash -c "%s"' % BD),
        ("busybox awk",     'busybox awk "%s"' % BD),
        # stdbuf value-flag retentions (attached / separated / long).
        ("stdbuf -oL bash -c",        'stdbuf -oL bash -c "%s"' % BD),
        ("stdbuf -o L bash -c",       'stdbuf -o L bash -c "%s"' % BD),
        ("stdbuf -eL sh -c",          'stdbuf -eL sh -c "%s"' % BD),
        ("stdbuf --output=L bash -c", 'stdbuf --output=L bash -c "%s"' % BD),
        # nested / double / triple recursion through busybox.
        ("busybox env FOO=x bash -c", 'busybox env FOO=x bash -c "%s"' % BD),
        ("busybox timeout 5 bash -c", 'busybox timeout 5 bash -c "%s"' % BD),
        ("busybox xargs bash -c (double)", 'busybox xargs bash -c "%s"' % BD),
        ("stdbuf -oL busybox sh -c (triple)", 'stdbuf -oL busybox sh -c "%s"' % BD),
        # existing wrapper retentions must NOT regress under the general attached-short-value walker.
        ("timeout -s9 bash -c",  'timeout -s9 bash -c "%s"' % BD),
        ("nice -n5 bash -c",     'nice -n5 bash -c "%s"' % BD),
        ("ionice -c2 bash -c",   'ionice -c2 bash -c "%s"' % BD),
        ("nocache -n3 bash -c",  'nocache -n3 bash -c "%s"' % BD),
        ("torsocks -d5 bash -c", 'torsocks -d5 bash -c "%s"' % BD),
        ("xargs -n1 bash -c",    'xargs -n1 bash -c "%s"' % BD),
        ("sudo -uroot bash -c",  'sudo -uroot bash -c "%s"' % BD),
        ("rlwrap -Oregexp bash -c", 'rlwrap -Oregexp bash -c "%s"' % BD),
        ("timeout -s 9 bash -c (sep)", 'timeout -s 9 bash -c "%s"' % BD),
        ("taskset -c 0-3 bash -c",  'taskset -c 0-3 bash -c "%s"' % BD),
    ])
    def test_retention(self, label, cmd):
        _retention(cmd)

    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        # ADVERSARIAL / patch-shifts-the-edge: a value-flag consuming the executor as its VALUE
        # leaves an unrecognized nested head -> FAIL-SAFE preserve -> CAUGHT (never freed).
        ("stdbuf -o bash -c (sep eats bash)", 'stdbuf -o bash -c "%s"' % BD),
        ("stdbuf -obash -c (attached eats)",  'stdbuf -obash -c "%s"' % BD),
        ("stdbuf -o 0 bash -c (sep val)",     'stdbuf -o 0 bash -c "%s"' % BD),
        # bundled-bool must NOT be read as an attached value: rlwrap -oc where -o is boolean ->
        # preserve (caught), NOT a mis-consumed value that would free the nested bash -c.
        ("rlwrap -oc bash -c (bundled-bool)", 'rlwrap -oc bash -c "%s"' % BD),
        # busybox bare with a directly-dangerous nested TOOL head (git) -> caught.
        ("busybox git branch -D (tool-head)", 'busybox git branch -D main'),
        # busybox executor leg in a compound stays caught (leg-locality).
        ("busybox sh -c compound leg", 'busybox sh -c "%s" ; echo ok' % BD),
    ])
    def test_adversarial_retention(self, label, cmd):
        _retention(cmd)

    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        # SANITY: direct executors / bare-token danger unchanged by F2 (True on both).
        ("bash -c",           'bash -c "%s"' % BD),
        ("timeout 5 bash -c", 'timeout 5 bash -c "%s"' % BD),
        ("sudo bash -c",      'sudo bash -c "%s"' % BD),
        ("bare git branch -D", BD),
        ("bare push --force",  PF),
    ])
    def test_sanity_unchanged(self, label, cmd):
        _retention(cmd)


# =========================================================================================
# CLASS C — STANDALONE-SHELL UNDER-BLOCK FIX (INTENDED base-False -> HEAD-True). Adding
# hush/lash/msh to _SHELL_STRING_EXECUTORS closes a PRE-EXISTING standalone under-block. These
# are pinned EXPLICITLY (not swept by monotonicity) — they are a fix, not a new over-block.
# =========================================================================================
class TestF2StandaloneShellUnderBlockFix:
    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        # `hush -c STR` genuinely EXECUTES STR -> catching the danger is CORRECT (under-block fix).
        ("hush -c danger", 'hush -c "%s"' % BD),
        ("lash -c danger", 'lash -c "%s"' % BD),
        ("msh -c danger",  'msh -c "%s"' % BD),
        # `hush danger` (NO -c; arg = a script FILENAME) -> the SAME conservative shell-arg
        # preserve every recognized shell already has (see the bash/sh anchor below). Now that
        # hush/lash/msh are recognized shells, they behave IDENTICALLY -> True. This is NOT a
        # new FAITHFUL-INERT over-block; it is matching-in-kind with the recognized-shell class.
        ("hush danger (no -c)", 'hush "%s"' % BD),
        ("lash danger (no -c)", 'lash "%s"' % BD),
        ("msh danger (no -c)",  'msh "%s"' % BD),
    ])
    def test_standalone_shell_now_caught(self, label, cmd):
        _standalone_underblock_fix(cmd)

    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        # The matching-in-kind REFERENCE class: a recognized shell with a bare arg is True==both.
        # hush/lash/msh "danger" (above) now behave exactly like these — not new-in-kind.
        ("bash danger (no -c)", 'bash "%s"' % BD),
        ("sh danger (no -c)",   'sh "%s"' % BD),
    ])
    def test_recognized_shell_anchor_true_both(self, label, cmd):
        _retention(cmd)


# =========================================================================================
# CLASS D — MONOTONICITY, FAITHFUL-INERT SCOPED. The busybox/stdbuf/walker machinery is
# strictly monotonic-toward-False: NO faithful inert click is newly blocked. Every row here is
# base-False; assert it STAYS False. DELIBERATELY EXCLUDES the hush/lash/msh transitions (Class
# C), which are INTENDED base-False->HEAD-True under-block fixes, not over-blocks.
# =========================================================================================
class TestF2MonotonicityFaithfulInert:
    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        ("busybox mycmd safe",   'busybox mycmd "hello world"'),
        ("stdbuf -oL ls",        'stdbuf -oL ls -la'),
        ("busybox ls",           'busybox ls -la'),
        ("timeout -s9 ls safe",  'timeout -s9 ls "note"'),
        ("nice -n5 echo",        'nice -n5 echo "hi"'),
        ("git status",           'git status'),
        ("bash -c safe",         'bash -c "echo hi"'),
        ("mycmd safe",           'mycmd "just a note"'),
        ("stdbuf -o0 mycmd safe", 'stdbuf -o0 mycmd "safe text"'),
    ])
    def test_no_new_over_block(self, label, cmd):
        assert D_BASE(cmd) is False, "fixture not base-False (mis-scoped monotonicity row): %r" % (cmd,)
        assert D(cmd) is False, "MONOTONICITY VIOLATION: F2 newly blocked a faithful inert click: %r" % (cmd,)


# =========================================================================================
# MUTANT-OF-LIVE-SOURCE — busybox RECURSE membership is a TESTED property. Remove `busybox`
# from _EXEC_WRAPPERS_RECURSE (late-binding: the classifier reads the module global at call
# time) and assert the `busybox bash -c "danger"` retention FLIPS True->False: without the
# recursion the nested executor's arg is stripped -> under-block. Proves the retention pin is
# load-bearing, not vacuous.
# =========================================================================================
def _load_mutant(mutate):
    src = (Path(__file__).parent.parent / "hooks" / "shared" / "merge_guard_common.py").read_text()
    mod = types.ModuleType("mgc_f2_mutant")
    mod.__file__ = str(Path(__file__).parent.parent / "hooks" / "shared" / "merge_guard_common.py")
    mod.__package__ = "shared"
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    mutate(mod)
    return mod


class TestF2MutantOfLiveSource:
    def test_busybox_recurse_membership_is_load_bearing(self):
        assert "busybox" in mgc._EXEC_WRAPPERS_RECURSE, "precondition: F2 added busybox to RECURSE"
        assert D('busybox bash -c "%s"' % BD) is True, "precondition: live catches busybox bash -c"
        m = _load_mutant(lambda mod: setattr(
            mod, "_EXEC_WRAPPERS_RECURSE", mod._EXEC_WRAPPERS_RECURSE - {"busybox"}))
        assert m.is_dangerous_command('busybox bash -c "%s"' % BD) is False, \
            "busybox RECURSE membership is NOT load-bearing (retention pin would be vacuous)"
