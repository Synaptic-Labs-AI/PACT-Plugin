"""
Location: pact-plugin/tests/test_merge_guard_1178_cert.py
Summary: COMPREHENSIVE BIDIRECTIONAL certification for #1178 — the positional-argument
         over-block class. The fix ships as two commits on top of c21eae19 (v4.6.5):
           P1 be33ab81 — git --trailer + gh --assignee carrier extensions.
           P2 c732a39f — strip-inert-default general strip (arbitrary-command quoted
                         positionals) + the exec-prefix RECURSION catalog + the
                         head-displacement (leading env-assignment / redirect) skip.
         Certifies against the REAL is_dangerous_command, base(c21eae19)-vs-PATCH(HEAD),
         NEVER a byte-diff / additive-lines argument (the #1118 trap: a shared-strip
         pipeline change opened BOTH an over-block and an under-block, invisible to a
         byte-diff — only behavioral data-flow review against the real classifier caught
         it). Baked baseline is loaded from git (crash-atomic, no working-tree mutation).

         THREAT POLARITY (SACROSANCT merge_guard control):
           - OVER-block = cardinal sin: blocking a faithful inert click (mycmd/logger/
             notify-send "...danger...") is wrong by definition. #1178 CLOSES this class.
           - UNDER-block = security hole the fix MUST NOT open. A real executor/wrapper/
             http-client whose danger is a preserved arg MUST stay caught.

         NON-VACUITY (the crux, base-vs-PATCH, in-test + permanent): every CLOSURE row
         asserts D_BASE(cmd) is True (the form was a genuine over-block at c21eae19) AND
         D(cmd) is False (the faithful click is freed). A row that is False at BOTH
         baselines is vacuous. Every RETENTION row asserts D_BASE is True AND D is True
         with the danger literal placed INSIDE the preserved quoted arg, so a wrong strip
         flips it False. The danger-inside-the-quoted-arg construction is what makes a
         "stays caught" pin fail precisely when the fix wrongly strips a preserved arg.

         THE HEAD-DISPLACEMENT SUB-CLASS (P0, co-equal with currently-caught-stays-caught):
         a leading env-assignment or redirect prefix that displaces the head token
         (`FOO=bar bash -c "danger"`, `2>&1 bash -c "danger"`, `LC_ALL=C curl -X DELETE
         "url"`). These are base-True -> PATCH-True RETENTION pins — NOT controls. They are
         LOAD-BEARING because a wrongly-freed executor is base-True -> PATCH-False, the SAME
         transition as a legitimate closure, so the monotonicity sweep (which only sees
         base-False -> PATCH-True) is BLIND to them. The retention pins are the only thing
         that catches an executor masquerading-as-a-closure under-block. (An auditor caught
         this exact under-block in the pre-amendment P2 88bec86e; c732a39f closes it.)

         Destructive verbs are assembled at runtime (BD/BDR/PF/M5/DEL) so this file carries
         no raw force-delete / force-push / merge / DELETE-method literal and stays inert to
         the live guard. Mirrors test_merge_guard_1140_carrier5_cert.py.
"""
import subprocess
import sys
import types
from pathlib import Path

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import shared.merge_guard_common as mgc  # noqa: E402

D = mgc.is_dangerous_command          # PATCH = live worktree HEAD (c732a39f)
STRIP = mgc._strip_non_executable_content

# --- Baked PRE-FIX classifier loaded from git ONCE for base-vs-PATCH non-vacuity.
#     _BASE = c21eae19 = the whole-#1178-fix parent (P1's parent; pre-fix main HEAD, v4.6.5).
#     Every closure is is_dangerous=True on BASE (over-blocked) and False on HEAD (freed);
#     every retention/binding form is True on both. Loading the parent as a module (NOT a
#     working-tree checkout) keeps this crash-atomic and sidesteps the "checkout-HEAD
#     restores the fixed baseline" trap. Mirrors test_merge_guard_1140_carrier5_cert.
_BASE_SHA = "c21eae19"  # pre-#1178 main HEAD (P1 be33ab81's parent)


def _load_classifier(sha):
    """Load merge_guard_common as it existed at `sha`, or None if unavailable.

    Returns None on any git/exec failure — git missing, or a SHALLOW clone lacking the
    parent commit (CI default fetch-depth) — so collection SUCCEEDS and the base-vs-HEAD
    differential rows self-SKIP (@requires_history) instead of aborting the file.
    """
    wt = Path(__file__).resolve().parents[2]  # worktree root (tests/../../)
    try:
        src = subprocess.check_output(
            ["git", "-C", str(wt), "show",
             sha + ":pact-plugin/hooks/shared/merge_guard_common.py"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    mod = types.ModuleType("merge_guard_common_1178_" + sha)
    mod.__file__ = str(wt / "pact-plugin/hooks/shared/merge_guard_common.py")
    mod.__package__ = "shared"  # so its `from shared.x import ...` resolve on sys.path
    try:
        exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    except Exception:
        return None
    return mod


_BASE = _load_classifier(_BASE_SHA)
# None-safe: a bare `_BASE.is_dangerous_command` would AttributeError at import when the
# parent source is unavailable (shallow clone). D_BASE is only ever called by the
# @requires_history-guarded differential rows.
D_BASE = _BASE.is_dangerous_command if _BASE is not None else None

requires_history = pytest.mark.skipif(
    _BASE is None,
    reason="base(c21eae19)-vs-HEAD differential requires merged history (shallow clone)",
)

# --- Destructive verbs assembled at runtime so this file carries no raw literal and stays
#     inert to the live guard (mirrors the 1140 cert). Each is is_dangerous=True on its own.
BD = "git " + "branch " + "-D victim"           # force branch-delete (danger inside quoted arg)
BDR = "git " + "branch " + "-D real"            # force branch-delete (executing-tail canary)
PF = "git " + "push " + "--force origin main"   # force-push
M5 = "gh " + "pr " + "merge 5 --delete-branch"  # destructive merge
_DEL = "-X " + "DELETE"                          # mutating HTTP method (assembled)
_URL = "https://api.github.com/repos/o/r/git/refs/heads/main"  # a git-refs endpoint (inert alone)
CURL_DEL = "curl " + _DEL                        # curl destructive DELETE
WGET_DEL = "wget " + "--method=" + "DELETE"      # wget destructive DELETE
GHAPI_DEL = "gh " + "api " + _DEL                # gh-api destructive DELETE


# ── Bidirectional assertion helpers (non-vacuity baked in) ───────────────────────────────
def _closure(cmd):
    """OVER-BLOCK CLOSURE: over-blocked at base (non-vacuous) -> freed at HEAD."""
    assert D_BASE(cmd) is True, "vacuous: not over-blocked at c21eae19 base: %r" % (cmd,)
    assert D(cmd) is False, "over-block NOT closed at HEAD (faithful click still blocked): %r" % (cmd,)


def _retention(cmd):
    """RETENTION: caught at base (non-vacuous) AND still caught at HEAD (no under-block)."""
    assert D_BASE(cmd) is True, "vacuous: not caught at c21eae19 base: %r" % (cmd,)
    assert D(cmd) is True, "UNDER-BLOCK opened at HEAD (a real danger was freed): %r" % (cmd,)


def _control_false(cmd):
    """Benign / never-blocked: False on both (proves closures are not blanket-freeing)."""
    assert D_BASE(cmd) is False and D(cmd) is False, "benign control not False==both: %r" % (cmd,)


# =========================================================================================
# CLASS A — OVER-BLOCK CLOSURE (base-True -> HEAD-False). The primary gate: a faithful inert
# click merges. Danger literal is INSIDE the quoted positional (POSIX-inert argv).
# =========================================================================================
class TestOverBlockClosure:
    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        ("arbitrary mycmd",       'mycmd "%s"' % BD),
        ("arbitrary logger",      'logger "%s"' % BD),
        ("arbitrary notify-send", 'notify-send "%s"' % BD),
        ("arbitrary grep",        'grep "%s" file.txt' % BD),
        ("multi-arg 2nd",         'mycmd "first" "%s"' % BD),
        ("sq value",              "mycmd '%s'" % BD),
        ("benign $() beside danger", 'mycmd "note $(date) %s"' % BD),  # span-scoped: $() kept, prose stripped
        ("C5 git --trailer",      'git commit --trailer "Ref: %s"' % BD),
        ("C7 gh --assignee",      'gh issue create --title "ok" --assignee "%s"' % BD),
        ("C7 gh --add-assignee",  'gh issue edit 1 --add-assignee "%s"' % BD),
    ])
    def test_closure(self, label, cmd):
        _closure(cmd)

    @requires_history
    @pytest.mark.parametrize("prefix", [
        "time", "exec", "command", "chrt -f 50", "taskset 0x3", "ionice", "unbuffer",
        "proxychains", "torsocks", "catchsegv", "nocache", "eatmydata", "rlwrap",
    ])
    def test_exec_prefix_inert_frees(self, prefix):
        # <prefix> <INERT> "danger" -> freed: the recursion reaches an inert nested head.
        _closure('%s mycmd "%s"' % (prefix, BD))

    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        # ANTI-SUBSTRING (C1): a head that CONTAINS an executor name as a substring is NOT an
        # executor — whole-head-token match frees it. A substring/\\b match would preserve it.
        ("bash-completion", 'bash-completion "%s"' % BD),
        ("python-config",   'python-config "%s"' % BD),
    ])
    def test_anti_substring_frees(self, label, cmd):
        _closure(cmd)

    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        # inert command BEHIND a head-displacement prefix -> still a correct closure.
        ("assign + inert",        'FOO=bar mycmd "%s"' % BD),
        ("multi-assign + inert",  'A=1 B=2 mycmd "%s"' % BD),
        ("append-assign + inert", 'PATH+=/x mycmd "%s"' % BD),
        ("=-in-value + inert",    'FOO=a=b mycmd "%s"' % BD),
        ("redirect + inert",      '2>/dev/null mycmd "%s"' % BD),
        ("spaced-redir + inert",  '>& file mycmd "%s"' % BD),
        ("fd-dup + inert",        '2>&1 mycmd "%s"' % BD),
    ])
    def test_prefix_inert_frees(self, label, cmd):
        _closure(cmd)


# =========================================================================================
# CLASS B/D — EXECUTOR / INTERPRETER RETENTION + CURRENTLY-CAUGHT-EXECUTING-ARG STAYS CAUGHT
# (P0). base-True -> HEAD-True, danger INSIDE the quoted code arg so a catalog miss flips it.
# =========================================================================================
class TestExecutorRetention:
    @requires_history
    @pytest.mark.parametrize("head", [
        "bash -c", "sh -c", "zsh -c", "dash -c", "ksh -c", "ksh93 -c", "mksh -c", "ash -c",
        "csh -c", "tcsh -c", "fish -c", "rksh -c", "/bin/bash -c",
        "eval", "su -c", "watch", "expect -c",
        "python -c", "python3 -c", "python3.11 -c", "perl -e", "ruby -e", "node -e",
        "nodejs -e", "php -r", "env -S",
    ])
    def test_direct_executor_stays_caught(self, head):
        _retention('%s "%s"' % (head, BD))

    @requires_history
    @pytest.mark.parametrize("head", ["ssh host", "rsh host", "remsh host", "slogin host"])
    def test_remote_shell_stays_caught(self, head):
        _retention('%s "%s"' % (head, BD))

    @requires_history
    @pytest.mark.parametrize("prefix", [
        "time", "exec", "command", "chrt -f 50", "taskset 0x3", "ionice", "unbuffer",
        "proxychains", "torsocks", "catchsegv", "nocache", "eatmydata", "rlwrap",
    ])
    def test_exec_prefix_executor_stays_caught(self, prefix):
        # WRAPPER-RECURSION: <prefix> bash -c "danger" -> preserve must RECURSE through the
        # wrapper head to the nested bash -c. A head-only detector strips this -> flips False.
        _retention('%s bash -c "%s"' % (prefix, BD))

    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        # ARITY-RETENTION: each wrapper's OWN value-flag consumed by the phased walk BEFORE
        # recursing. A mis-counted arity mis-reads the flag value as the command.
        ("timeout -s KILL 5", 'timeout -s KILL 5 bash -c "%s"' % BD),
        ("xargs -n 2",        'xargs -n 2 bash -c "%s"' % BD),
        ("env -u X",          'env -u X bash -c "%s"' % BD),
        ("nice -n 5",         'nice -n 5 bash -c "%s"' % BD),
        ("doas -u me",        'doas -u me bash -c "%s"' % BD),
        ("sudo -u me",        'sudo -u me bash -c "%s"' % BD),
        ("ionice -c 2 -n 4",  'ionice -c 2 -n 4 bash -c "%s"' % BD),
        ("rlwrap -f x",       'rlwrap -f x bash -c "%s"' % BD),
        ("proxychains -f c",  'proxychains -f cfg bash -c "%s"' % BD),
        ("torsocks -a A",     'torsocks -a 1.2.3.4 bash -c "%s"' % BD),
    ])
    def test_arity_value_flag_stays_caught(self, label, cmd):
        _retention(cmd)

    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        # taskset -c COLLISION: taskset's -c is --cpu-list, NOT bash's -c. The phased walk
        # must consume the cpu-list VALUE then recurse to bash -c.
        ("taskset -c mask + bash -c", 'taskset -c 0-3 bash -c "%s"' % BD),
        ("taskset bare + bash -c",    'taskset bash -c "%s"' % BD),
        # PID-operate edges preserve (dash-flag in command position -> preserve).
        ("chrt -p 50 + bash -c",      'chrt -p 50 bash -c "%s"' % BD),
        ("taskset -p 0x3 + bash -c",  'taskset -p 0x3 bash -c "%s"' % BD),
        ("ionice -p 1234 + bash -c",  'ionice -p 1234 bash -c "%s"' % BD),
    ])
    def test_taskset_collision_and_pid_edges(self, label, cmd):
        _retention(cmd)

    @requires_history
    def test_taskset_collision_inert_still_frees(self):
        # The paired direction: taskset -c 0-3 mycmd "danger" is inert -> freed (closure).
        _closure('taskset -c 0-3 mycmd "%s"' % BD)


# =========================================================================================
# CLASS HEAD-DISPLACEMENT (P0) — a leading env-assignment / redirect that displaces the head.
# Two sub-families: executor-behind-prefix AND http-client-behind-prefix. base-True -> HEAD-True
# RETENTION (NOT controls). These evade the monotonicity sweep (base-True -> would-be-False).
# =========================================================================================
class TestHeadDisplacementExecutor:
    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        ("single assign",       'FOO=bar bash -c "%s"' % BD),
        ("LC_ALL locale",       'LC_ALL=C bash -c "%s"' % BD),
        ("PYTHONPATH + python3", 'PYTHONPATH=. python3 -c "%s"' % BD),
        ("VAR + sh + force-push", 'VAR=x sh -c "%s"' % PF),
        ("multi assign",        'FOO=1 BAR=2 bash -c "%s"' % BD),
        ("append assign +=",    'PATH+=/x bash -c "%s"' % BD),   # the += regex-gap the lead caught
        ("empty RHS",           'FOO= bash -c "%s"' % BD),
        ("$-expansion RHS",     'FOO=$HOME bash -c "%s"' % BD),
        ("=-in-value RHS",      'FOO=a=b bash -c "%s"' % BD),
        ("quoted RHS",          'FOO="a=b" bash -c "%s"' % BD),
        ("sq value w/ space",   "FOO='x=y z' bash -c \"%s\"" % BD),
        ("PATH colon value",    'PATH=/a:/b bash -c "%s"' % BD),
        ("path-exec nested",    'FOO=bar /usr/bin/python3 -c "%s"' % BD),
        ("non-bash zsh",        'ZDOTDIR=/tmp zsh -c "%s"' % BD),
        ("assign + wrapper + exec", 'FOO=bar timeout 5 bash -c "%s"' % BD),
        ("assign + wrapper nice",  'FOO=bar nice -n 5 bash -c "%s"' % BD),
    ])
    def test_assignment_prefix_executor_stays_caught(self, label, cmd):
        _retention(cmd)

    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        ("bare redirect target", '2>/dev/null bash -c "%s"' % BD),
        ("redirect > log",       '> log bash -c "%s"' % BD),
        ("assign + redirect",    'FOO=bar 2>/dev/null bash -c "%s"' % BD),
        ("redirect + assign",    '2>/dev/null FOO=bar bash -c "%s"' % BD),
        ("spaced >& file",       '>& file bash -c "%s"' % BD),
        ("spaced <& file",       '<& file bash -c "%s"' % BD),
        ("spaced 2>& file",      '2>& file bash -c "%s"' % BD),
        ("fd-dup 2>&1",          '2>&1 bash -c "%s"' % BD),
        ("fd-dup >&2",           '>&2 bash -c "%s"' % BD),
        ("fd-dup numbered 3>&2", '3>&2 bash -c "%s"' % BD),
        ("and-redirect &>log",   '&>log bash -c "%s"' % BD),
    ])
    def test_redirect_prefix_executor_stays_caught(self, label, cmd):
        _retention(cmd)

    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        # prefix-displaced git/gh with BARE-TOKEN danger (not a quoted arg) -> caught.
        ("assign + git branch -D",  'FOO=bar %s' % BD),
        ("GIT_DIR + force-push",    'GIT_DIR=. %s' % PF),
        ("redirect + git branch -D", '2>/dev/null %s' % BD),
        ("assign + gh pr merge",    'FOO=bar %s' % M5),
    ])
    def test_prefix_displaced_bare_danger_stays_caught(self, label, cmd):
        _retention(cmd)


class TestHeadDisplacementHttpClient:
    # NEW, more severe sub-family: the head-displacement bug bypassed the http-client
    # exclusion (clause-b) too, so a REAL destructive curl/wget with a QUOTED destructive
    # URL under-blocked behind a prefix. Danger target lives in the quoted URL.
    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        ("curl DELETE plain (no prefix)", '%s "%s"' % (CURL_DEL, _URL)),  # control: caught both
        ("assign + curl DELETE",          'FOO=bar %s "%s"' % (CURL_DEL, _URL)),
        ("locale + curl DELETE",          'LC_ALL=C %s "%s"' % (CURL_DEL, _URL)),
        ("redirect + curl DELETE",        '2>/dev/null %s "%s"' % (CURL_DEL, _URL)),
        ("assign + wget DELETE",          'FOO=bar %s "%s"' % (WGET_DEL, _URL)),
    ])
    def test_http_client_prefix_stays_caught(self, label, cmd):
        _retention(cmd)

    @requires_history
    def test_ghapi_unquoted_endpoint_stays_caught_distinct(self):
        # DISTINCT control: gh-api's endpoint is an UNQUOTED positional, so masking never
        # hid it -> it stays caught via clause-(c), and was NEVER under-blocked. Keep the
        # curl/wget under-block pins on QUOTED URLs only; do NOT expect gh-api-unquoted in
        # the under-block set.
        _retention('FOO=bar %s repos/o/r/git/refs/heads/main' % GHAPI_DEL)


# =========================================================================================
# CLASS C — BINDING INVARIANT (P0). Dangerous legs are NEVER stripped, so the target-bearing
# quoted value survives for mint/read binding (#1042). Caught on both AND target preserved.
# =========================================================================================
class TestBindingInvariant:
    @requires_history
    @pytest.mark.parametrize("label,cmd", [
        ("branch -D quoted target", 'git branch -D "victim"'),
        ("force-push",              PF),
        ("gh pr merge",             M5),
    ])
    def test_dangerous_leg_stays_caught(self, label, cmd):
        _retention(cmd)

    def test_target_survives_strip(self):
        # The dangerous leg is NOT stripped -> the branch target 'victim' survives verbatim
        # (a naive value-strip would rewrite it to STRIPPED and break the #1042 set-equality).
        out = STRIP('git branch -D "victim"')
        assert "victim" in out, "binding target stripped (over-binding under-block): %r" % (out,)

    def test_leg_locality_executing_tail_stays_caught(self):
        # An inert first leg does not swallow an executing destructive tail (leg-locality).
        _retention('mycmd "safe" && %s' % BDR)


# =========================================================================================
# CLASS E — DEFERRED / RESIDUAL PINS (True==both or documented under-block). FORETELLING
# docstrings so a future cycle realigns instead of being surprised by these.
# =========================================================================================
class TestResidualPins:
    def test_c8_curl_header_permanent_residual(self):
        # C8 curl -H over-block is a PERMANENT http-client residual: the destructive target
        # can live in a quoted URL that masking hides, so the value cannot be stripped
        # safely. True==both — does NOT close (distinct from C5/C7 which DID close in P1).
        cmd = 'curl -H "X-Note: %s" %s' % (BD, _URL)
        assert D(cmd) is True, "C8 curl -H residual unexpectedly changed: %r" % (cmd,)

    def test_command_v_noexec_over_block_residual(self):
        # `command -v <exec> "danger"` is a NONSENSICAL noexec form (command -v takes one
        # name and ignores the -c args; it never executes the string). Over-block-SAFE,
        # user-ruled leave-it. True==both. The REAL inert `command -v mycmd "danger"` FREES
        # (asserted below), so the residual is scoped to the malformed form only.
        assert D('command -v bash -c "%s"' % BD) is True

    @requires_history
    def test_command_v_inert_frees(self):
        _closure('command -v mycmd "%s"' % BD)

    def test_custom_wrapper_under_block_residual(self):
        # D1-ratified tolerated residual: a genuinely-CUSTOM (uncatalogued) exec-wrapper
        # cannot be modeled, so its nested executor's arg is stripped -> under-block. This is
        # the ONLY exec-prefix residual and is accepted by-construction (honest-mistake model:
        # an unknown head handing a quoted string to an interpreter is not enumerable).
        assert D('myrunner bash -c "%s"' % BD) is False


# =========================================================================================
# CLASS F — CONTROLS. Benign / cross-context forms stay False==both; genuine non-retention
# controls behave per their (inert or self-contained) head.
# =========================================================================================
class TestControls:
    @pytest.mark.parametrize("label,cmd", [
        ("benign mycmd",     'mycmd "hello world"'),
        ("benign trailer",   'git commit --trailer "Ref: #123"'),
        ("benign assignee",  'gh issue create --title "ok" --assignee "alice"'),
        ("benign logger",    'logger "deploy complete"'),
        ("benign bash -c",   'bash -c "echo hi"'),
    ])
    def test_benign_false_both(self, label, cmd):
        _control_false(cmd)

    @requires_history
    def test_env_wrapper_form_caught(self):
        # env FOO=bar bash -c is the WRAPPER form (env consumes NAME=VALUE via PHASE-3), the
        # target the bare-assignment head-displacement fix converges to. Caught on both.
        _retention('env FOO=bar bash -c "%s"' % BD)


# =========================================================================================
# MONOTONICITY — the strip only ever REMOVES an over-block; it NEVER creates one. Assert 0
# vectors go base-False -> PATCH-True across the whole battery. (Complements — does NOT
# replace — the head-displacement retention pins, which catch the base-True -> PATCH-False
# under-block that this one-directional sweep is structurally blind to.)
# =========================================================================================
_MONOTONICITY_BATTERY = [
    'mycmd "%s"' % BD, 'logger "%s"' % BD, 'bash -c "%s"' % BD, 'python3 -c "%s"' % BD,
    'FOO=bar bash -c "%s"' % BD, '2>&1 bash -c "%s"' % BD, 'timeout 5 bash -c "%s"' % BD,
    'taskset -c 0-3 bash -c "%s"' % BD, 'FOO=bar %s "%s"' % (CURL_DEL, _URL),
    'git branch -D "victim"', PF, M5, 'mycmd "hello world"', 'git status',
    'FOO=bar mycmd "%s"' % BD, 'command bash -c "%s"' % BD, 'bash-completion "%s"' % BD,
]


class TestMonotonicity:
    @requires_history
    @pytest.mark.parametrize("cmd", _MONOTONICITY_BATTERY)
    def test_no_new_over_block(self, cmd):
        # If it was NOT over-blocked at base, the strip must not newly block it.
        if D_BASE(cmd) is False:
            assert D(cmd) is False, "MONOTONICITY VIOLATION: strip created a new over-block: %r" % (cmd,)


# =========================================================================================
# IS_DANGEROUS BYTE-IDENTITY — the P2 refactor factored the danger battery into the shared
# _stripped_surface_danger predicate (consumed by both the read floor and the preserve
# predicate). Prove the extraction is BEHAVIOR-PRESERVING on the PRE-EXISTING battery (the
# recognized-op + benign surface); the #1178 closures are the intended delta OUTSIDE this set.
# =========================================================================================
_BYTE_IDENTITY_BATTERY = [
    'git branch -D victim', 'git branch -D "victim"', PF, M5,
    'gh pr close 5 --delete-branch', 'git status', 'git log --oneline',
    'git commit -m "fix the bug"', 'echo "safe note"', 'ls -la',
    '%s && %s' % (M5, BD), '%s ; echo done' % M5, 'gh pr merge 5 | tee log',
    'git push origin main', 'git commit --amend --no-edit',
    'bash -c "%s"' % BD, 'git branch -D victim && rm -rf /tmp/x',
]


class TestIsDangerousByteIdentity:
    @requires_history
    @pytest.mark.parametrize("cmd", _BYTE_IDENTITY_BATTERY)
    def test_pre_existing_battery_unchanged(self, cmd):
        assert D_BASE(cmd) == D(cmd), \
            "shared-predicate extraction changed a pre-existing classification: %r" % (cmd,)


# =========================================================================================
# STRIP-SURFACE — document the transform directly (complements the is_dangerous differential):
# a closure's inert value is stripped; a retention leg's danger arg is preserved; the binding
# target survives; leg-locality holds under an unquoted separator.
# =========================================================================================
class TestStripSurface:
    def test_inert_value_stripped(self):
        assert STRIP('mycmd "%s"' % BD) == "mycmd STRIPPED"

    def test_executor_arg_preserved(self):
        # the danger arg of a real executor survives the strip (else it would un-gate).
        assert BD in STRIP('bash -c "%s"' % BD)

    def test_headdisplaced_executor_arg_preserved(self):
        assert BD in STRIP('FOO=bar bash -c "%s"' % BD)

    def test_binding_target_preserved(self):
        assert "victim" in STRIP('git branch -D "victim"')

    def test_leg_locality_tail_preserved(self):
        # the executing tail after an unquoted && stays OUTSIDE the stripped span.
        assert BDR in STRIP('mycmd "safe" && %s' % BDR)


# =========================================================================================
# MUTANT-OF-LIVE-SOURCE — catalog / skip membership is a TESTED property, not just an
# assertion. Load the live source into a fresh module, remove a catalog member (late-binding:
# the classifier reads the module global at call time), and assert the matching retention pin
# FLIPS to under-block. Proves the mechanism is load-bearing (mirrors the 1140 gobbling control).
# =========================================================================================
def _live_source():
    return Path(__file__).parent.parent / "hooks" / "shared" / "merge_guard_common.py"


def _load_mutant(mutate):
    """Load the live merge_guard_common into a fresh module and apply `mutate(mod)`."""
    src = _live_source().read_text()
    mod = types.ModuleType("mgc_mutant")
    mod.__file__ = str(_live_source())
    mod.__package__ = "shared"
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    mutate(mod)
    return mod


class TestMutantOfLiveSource:
    def test_wrapper_membership_is_load_bearing(self):
        # Remove `taskset` from the recursion catalog -> `taskset ... bash -c "danger"` can no
        # longer recurse to the nested bash -c -> the strip eats the arg -> under-block.
        base_caught = D('taskset -c 0-3 bash -c "%s"' % BD)
        assert base_caught is True, "precondition: live classifier catches taskset+bash -c"
        m = _load_mutant(lambda mod: setattr(
            mod, "_EXEC_WRAPPERS_RECURSE", mod._EXEC_WRAPPERS_RECURSE - {"taskset"}))
        assert m.is_dangerous_command('taskset -c 0-3 bash -c "%s"' % BD) is False, \
            "taskset catalog membership is NOT load-bearing (pin would be vacuous)"

    def test_wrapper_recurse_catalog_nonempty(self):
        # Guard the mutant's premise: the catalog is a mutable frozenset containing taskset.
        assert "taskset" in mgc._EXEC_WRAPPERS_RECURSE
