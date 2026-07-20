"""
Location: pact-plugin/tests/test_merge_guard_1203_cert.py
Summary: The DURABLE five-dimension certification for the #1203 over-block fix — the
         TARGET-BLIND SENTINEL mint for the implicit current-branch force-push (C3) and
         the bare `gh pr merge` (C3b), plus the generalized inert-help recognizer (C1)
         and its short-alias value-flag skip (C1b). Certifies against the REAL classifier
         + REAL post/pre hooks, base (committed vendored fixture at 5017d1f2 — the #1203
         PARENT — via merge_guard_baseline_loader.load_baseline_5017d1f2; loud-fail,
         CI-executable, never skip) vs live HEAD. NEVER a byte-diff / git-show-by-SHA at
         assertion time.

GOVERNING MODEL — SACROSANCT, GOOD-FAITH. The merge-guard routes an HONEST destructive
command through the user's approval click; a faithful single-command click always mints
and self-authorizes. The ONLY cardinal sin is the OVER-BLOCK: blocking a faithful click,
INCLUDING gating it but leaving it unmintable (gated-but-unmintable). The #1203 arc closes
FOUR over-blocks: (1) the implicit current-branch force-push (`git push --force`), gated-
but-unmintable at 5017d1f2 because its destination is runtime state absent from the command
string; (2) the bare `gh pr merge`, same shape (current-branch PR unresolvable in-hook);
(3) the inert `gh pr merge --help` / `git push --force --help`, gated though it merges/pushes
NOTHING; (4) the C1b short-alias value-decoy (`gh pr merge 5 -t -h`), an under-block C1
briefly INTRODUCED and C1b closed WITHIN the arc.

WHY TARGET-BLIND SENTINEL (KD-1/KD-2). An implicit force-push target is config-dependent
(push.default + upstream can rename the ref or make it multi-ref), so a runtime-resolve to a
concrete branch would MINT THE WRONG TARGET and LAUNDER — a `git push --force` token resolved
to `main` would cross-authorize an explicit `git push --force origin main`. The fix mints a
DISTINCT, NUL-framed, target-blind key (`force_push_implicit` / `merge_implicit`, each a
NUL-prefixed constant that TestCorpusShape pins distinct) in a mutually-exclusive populate-site (KD-4: reached ONLY when the explicit target keys target_ref
AND force_push_set are BOTH absent, on op_type=='force-push', 0/1-positional only). The NUL byte
can NEVER appear in a real target_ref/pr_number, and the distinct KEY is never compared against
target_ref/pr_number — so an implicit-form token can NOT cross-authorize an explicit
`--force origin main` / a numbered `gh pr merge 42`, and force-push ⊥ merge (distinct keys +
op-type-first). is_dangerous stays True for every minted form — this is target-PRECISION, NOT
un-gating (the floor).

THE FIVE DIMENSIONS, each with a demonstrated known-bad:
  (a) MINT              — every MUST_MINT form MINTS a token AND is_dangerous stays True.
                          Base known-bad: gated-but-unmintable (dang True, no mint key).
  (b) OFF-TOKEN ROUND-  — identity read OFF the minted token via a REAL post_main -> pre_main
      TRIP               round-trip (never off extract_command_context(command)); compound
                          `cd && …` rows included. Known-bad: the pipeline-blindness trap —
                          the exec-WITHOUT-token DENY is the fix-coupled leg.
  (c) NO-CROSS-AUTH     — force_push_implicit ⊥ explicit-ref, merge_implicit ⊥ number,
                          force_push_implicit ⊥ merge_implicit, ⊥ cross-op. Discriminating
                          known-bad: simulate the REJECTED runtime-resolve (bind target_ref=
                          'main' for the implicit form) -> the implicit token cross-authorizes
                          `--force origin main` (proves the target-blind distinct key is load-
                          bearing). ACCEPTED residual (KD-3): the target-blind sentinel + the
                          existing max_uses=2 lifecycle means a 2nd use rides to a DIFFERENT
                          current branch — ALLOWED and documented, NOT a re-prompt.
  (d) INERT-NOT-GATED   — MUST_STAY_INERT un-gates (base known-bad = gated at 5017d1f2) + the
                          C1b short-alias matrix (long-gates / short-gates / bare-`-h`-un-gates
                          for {-t,-b,-F,-A,-c,-R,--repo}, -h AND --help, merge AND close).
                          C1b known-bad = SUPPLEMENT-REVERT (neuter _INERT_HELP_EXTRA_VALUE_
                          FLAGS -> `-t -h` un-gates) — the C1b under-block was born+closed
                          WITHIN the arc, so it does NOT show against the 5017d1f2 base.
  (e) CONTROL-UNCHANGED — explicit ref'd forms + `gh pr merge 42` byte-identical base->HEAD;
                          the scalar/set extractors are untouched. Known-bad: a control whose
                          identity changed base->HEAD would mean the fix perturbed the explicit
                          path.

NON-VACUITY DISCIPLINE (carried first-hand from test_merge_guard_1134_cert.py + the close-arc):
every assertion is demonstrated FAILING on a known-bad; identity is read OFF the token, never
off the whole-command extract; token-dir isolation for any mint LOOP via an owned-dir helper
(the leak bit twice historically); base-vs-frozen-fixture differential, NEVER a byte-diff.
Known-bads are verified to ACTUALLY flip the row (a "does-not-cross-authorize" pin is vacuous
if the neuter never reaches the guarded branch).

ACCEPTED RESIDUALS (documented, NOT defects — do not "fix" to green):
  * SET B non-runnable ref-positional force-push (`git push --force HEAD` / `main` / `:main` /
    `HEAD:main` / `refs/heads/main` / `+main`): git parses the sole positional as <repository>
    -> fatal 128, so there is NO faithful click; they stay gated-but-unmintable. A mint-on-no-
    target fix would ALSO harmlessly over-mint them (error before push) — BOTH dispositions are
    acceptable; we pin the actual (still gated-but-unmintable) and document the other.
  * max_uses=2 cross-branch 2nd-use ride (KD-3): asserted ALLOWED + documented below.

Destructive verbs are assembled at runtime so this file stays inert to the live guard.
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
from merge_guard_baseline_loader import load_baseline_5017d1f2  # noqa: E402
from merge_guard_post import main as post_main  # noqa: E402
from merge_guard_pre import main as pre_main  # noqa: E402

# Runtime-assembled command stems (kept out of one literal so this file is inert).
_PG = "git " + "push "
_GH = "gh " + "pr "
_PREFIX = "cd /repo && "

D = mgc.is_dangerous_command
DETECT = mgc.detect_command_operation_type
FP_SENTINEL = mgc._FORCE_PUSH_IMPLICIT_SENTINEL
MERGE_SENTINEL = mgc._MERGE_IMPLICIT_SENTINEL


def _fpq(*positionals):
    """The cycle-4 NETSTRING-qualified force_push_implicit identity for a 1-positional implicit
    force-push: `SENTINEL + _canonical_join([<remote-or-url-or-refspec>])` (len-prefixed, e.g.
    `SENTINEL + '6:origin'`). Derived through the REAL _canonical_join SSOT (not a hardcoded
    `:remote`), so a netstring-format change is reflected here and the identity stays injective."""
    return FP_SENTINEL + mgc._canonical_join(list(positionals))


def _base():
    """The pre-#1203 (5017d1f2) classifier's is_dangerous_command."""
    return load_baseline_5017d1f2().is_dangerous_command


def _base_ctx():
    return load_baseline_5017d1f2().extract_command_context


# The identity keys a token may bind (the which-key-won read).
_TARGET_KEYS = (
    "pr_number", "branch", "branch_set", "target_ref", "push_set",
    "force_push_set", "force_push_implicit", "merge_implicit",
    "mass_target", "protected_branch",
)


def _mint_keys(ctx):
    return [k for k in _TARGET_KEYS if ctx.get(k)]


# ═════════════════════════════════════════════════════════════════════════════════
# CORPUS — module-level data, the cert IS the corpus. Row-count pinned below.
# ═════════════════════════════════════════════════════════════════════════════════
# MUST_MINT force-push (6 RUNNABLE implicit forms) — each mints force_push_implicit.
MUST_MINT_FORCE_PUSH = [
    _PG + "--force",
    _PG + "-f",
    _PG + "-f origin",
    _PG + "--force origin",
    "git -c user.name=x push --force",
    _PREFIX + _PG + "--force",
]

# cycle-3 (findings #1+#2 tighten): the force_push_implicit identity is REMOTE-QUALIFIED for
# the 1-positional remote-only form (`--force origin` -> SENTINEL:origin), so a `--force origin`
# approval no longer authorizes a `--force upstream` (cross-remote). The 0-positional truly-
# implicit forms (bare `--force`/`-f`, global-flag, non-first-leg) keep the PLAIN sentinel. This
# maps each MUST_MINT_FORCE_PUSH row to its expected identity value (pinned, not inferred).
FP_IMPLICIT_VALUE = {
    _PG + "--force": FP_SENTINEL,
    _PG + "-f": FP_SENTINEL,
    _PG + "-f origin": _fpq("origin"),                # cycle-4: netstring-qualified
    _PG + "--force origin": _fpq("origin"),           # cycle-4: netstring-qualified
    "git -c user.name=x push --force": FP_SENTINEL,
    _PREFIX + _PG + "--force": FP_SENTINEL,
}

# MUST_MINT bare merge (faithful, gh infers the current-branch PR) — merge_implicit.
MUST_MINT_MERGE = [
    _GH + "merge",
    _GH + "merge --admin",
    _GH + "merge --squash",
]

# MUST_STAY_INERT — un-gated at HEAD (help short-circuits, mutates nothing); GATED at base.
MUST_STAY_INERT = [
    _GH + "merge --help",
    _GH + "merge -h",
    "gh " + "help " + "pr " + "merge",
    _PG + "--force --help",
    _GH + "close -d --help",   # close WITH delete-branch (dangerous at base) -> inert at head
]

# DONT_CARE_RESIDUAL — SET B non-runnable ref-positional force-push (fatal 128). Pinned as
# gated-but-unmintable (the accepted disposition); harmless-over-mint would also be acceptable.
DONT_CARE_RESIDUAL = [
    _PG + "--force HEAD",
    _PG + "--force main",
    _PG + "--force :main",
    _PG + "--force HEAD:main",
    _PG + "--force refs/heads/main",
    _PG + "--force +main",
]

# CONTROL_UNCHANGED — explicit ref'd forms + numbered merge. (cmd, expected_key, expected_value)
CONTROL_UNCHANGED = [
    (_PG + "--force origin main", "target_ref", "main"),
    (_PG + "-f origin main", "target_ref", "main"),
    (_PG + "--force origin HEAD:main", "target_ref", "main"),
    (_PG + "--force origin +main", "target_ref", "+main"),
    (_GH + "merge 42", "pr_number", "42"),
]

# ── C1b short-alias value-decoy matrix ─────────────────────────────────────────
# The gh pr merge/close value-taking flags whose short aliases C1b skips. Each
# `<flag> -h` / `<flag> --help` binds the -h/--help as the flag's VALUE => a REAL
# destructive command that MUST gate; only a BARE -h/--help is a help short-circuit.
_C1B_MERGE_VALUE_SHORTS = ["-t", "-b", "-F", "-A", "-R"]
_C1B_MERGE_VALUE_LONGS = ["--subject", "--body", "--body-file", "--author-email", "--repo"]
# close is destructive only with -d; its value-shorts are -c(--comment) and -R(--repo).
_C1B_CLOSE_VALUE_SHORTS = ["-c", "-R"]

# Boolean shorts whose following -h is a REAL bare help (must STAY inert — a skip here
# would re-introduce an over-block).
_C1B_MERGE_BOOL_SHORTS = ["-d", "-s", "-r", "-m"]


def _c1b_must_gate_rows():
    """(cmd, label) for every short/long value-flag decoy that MUST gate at HEAD."""
    rows = []
    for dv in ("-h", "--help"):
        for f in _C1B_MERGE_VALUE_SHORTS + _C1B_MERGE_VALUE_LONGS:
            rows.append((_GH + "merge 5 " + f + " " + dv, "merge %s %s" % (f, dv)))
        for f in _C1B_CLOSE_VALUE_SHORTS:
            rows.append((_GH + "close 5 -d " + f + " " + dv, "close -d %s %s" % (f, dv)))
    return rows


C1B_MUST_GATE = _c1b_must_gate_rows()
C1B_MUST_STAY_INERT = [_GH + "merge 5 " + b + " -h" for b in _C1B_MERGE_BOOL_SHORTS]


# ═════════════════════════════════════════════════════════════════════════════════
# REAL post_main -> pre_main drivers (copied from the 1134 cert harness pattern).
# _mint returns (count, token_context) — context read OFF the minted token on disk.
# ═════════════════════════════════════════════════════════════════════════════════
_ALLOW, _DENY = 0, 2


def _mint(cmd, tok):
    """Drive the REAL post hook with an approval embedding `cmd`. Returns
    (count_of_tokens_minted, context_dict_or_None) read from the token FILE."""
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
        "session_id": "cert-1203",
    })
    with patch.object(mgpost, "TOKEN_DIR", tok), \
            patch("sys.stdin", io.StringIO(env)), \
            patch("sys.stdout", io.StringIO()):
        try:
            post_main()
        except SystemExit as e:
            assert e.code == 0, "post hook exited nonzero: %r" % (e.code,)
    new = set(tok.glob("merge-authorized-*")) - before
    if len(new) != 1:
        return len(new), None
    ctx = json.loads(next(iter(new)).read_text()).get("context", {})
    return 1, ctx


def _execute(cmd, tok):
    """Run `cmd` through the REAL pre hook; return exit code (0=ALLOW, 2=DENY)."""
    env = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "session_id": "cert-1203",
    })
    with patch.object(mgpre, "TOKEN_DIR", tok), \
            patch("sys.stdin", io.StringIO(env)), \
            patch("sys.stdout", io.StringIO()), \
            patch("sys.stderr", io.StringIO()):
        try:
            pre_main()
            return _ALLOW
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else _ALLOW


def _won_key(ctx):
    keys = _mint_keys(ctx)
    return keys[0] if len(keys) == 1 else keys


def _isolated_roundtrip(approve, execute):
    """Mint `approve`, then execute `execute`, in a token dir this call OWNS — so it is
    STRUCTURALLY IMPOSSIBLE to share across rows (the token-leak that bit the 1134 cert
    twice cannot recur). Returns (minted, rc)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tok = Path(d)
        minted, _ = _mint(approve, tok)
        assert minted == 1, "the approval did not mint (round-trip is vacuous): %r" % approve
        return minted, _execute(execute, tok)


# ═════════════════════════════════════════════════════════════════════════════════
# CORPUS SHAPE — silent-drift guard.
# ═════════════════════════════════════════════════════════════════════════════════
class TestCorpusShape:
    def test_row_counts_are_pinned(self):
        assert len(MUST_MINT_FORCE_PUSH) == 6, "MUST_MINT_FORCE_PUSH drifted from 6"
        assert len(MUST_MINT_MERGE) == 3, "MUST_MINT_MERGE drifted from 3"
        assert len(MUST_STAY_INERT) == 5, "MUST_STAY_INERT drifted from 5"
        assert len(DONT_CARE_RESIDUAL) == 6, "DONT_CARE_RESIDUAL (SET B) drifted from 6"
        assert len(CONTROL_UNCHANGED) == 5, "CONTROL_UNCHANGED drifted from 5"
        # C1b matrix: (5 short + 5 long merge + 2 close) x 2 decoy spellings = 24.
        assert len(C1B_MUST_GATE) == 24, "C1b matrix drifted from 24"
        assert len(C1B_MUST_STAY_INERT) == 4, "C1b boolean-inert drifted from 4"

    def test_sentinels_are_nul_framed_and_distinct(self):
        assert FP_SENTINEL != MERGE_SENTINEL, "the two sentinels must be DISTINCT"
        assert "\x00" in FP_SENTINEL and "\x00" in MERGE_SENTINEL, (
            "sentinels must be NUL-framed (a NUL can never appear in a real ref/pr_number)"
        )

    def test_review_update_corpus_counts_pinned(self):
        # M1 + F1 + close-output corpora (added in the peer-review cert-update).
        assert len(MERGE_TARGET) == 5, "MERGE_TARGET drifted from 5"
        assert len(MERGE_BARE) == 5, "MERGE_BARE drifted from 5"
        assert len(MERGE_CROSS_AUTH) == 7, "MERGE_CROSS_AUTH drifted from 7"
        assert len(F1_CROSS_AUTH) == 4, "F1_CROSS_AUTH drifted from 4"
        assert len(CLOSE_OUTPUT_CORPUS) == 4, "CLOSE_OUTPUT_CORPUS drifted from 4"
        # Finding #4 (both arms): 6 head-form + 4 flag-loop = 10 GIT gate forms.
        assert len(F4_HEAD_FORM) == 6 and len(F4_FLAG_LOOP) == 4 and len(F4_GATE) == 10, "F4 gate corpus drifted"
        assert len(F4_INERT) == 6 and len(F4_DECOY_GATED) == 3, "F4 control corpus drifted"
        # cycle-3 (remote-qualified force-push identity).
        assert len(CYCLE3_IDENTITY) == 6 and len(CYCLE3_CROSS_AUTH) == 4, "cycle-3 corpus drifted"
        assert len(FP_IMPLICIT_VALUE) == len(MUST_MINT_FORCE_PUSH) == 6, "FP_IMPLICIT_VALUE map drifted from the corpus"


# ═════════════════════════════════════════════════════════════════════════════════
# (a) MINT — every MUST_MINT form mints + is_dangerous stays True (target-precision).
# ═════════════════════════════════════════════════════════════════════════════════
class TestDimAMintForcePush:
    """Every runnable implicit force-push: gated at head AND base (is_dangerous True — NOT
    un-gated), UNMINTABLE at base (the over-block: dang True but no mint key), and at head
    mints exactly one token binding op=force-push on the target-blind sentinel, self-authorizing."""

    @pytest.mark.parametrize("cmd", MUST_MINT_FORCE_PUSH, ids=MUST_MINT_FORCE_PUSH)
    def test_mints_force_push_implicit(self, cmd, tmp_path):
        assert D(cmd) is True, "implicit force-push not gated at head (floor lost): %r" % cmd
        assert _base()(cmd) is True, "row not dangerous at base — not a gated-but-unmintable row: %r" % cmd
        # KNOWN-BAD (base): gated but binds NO mint key -> the over-block.
        assert _mint_keys(_base_ctx()(cmd)) == [], (
            "base already binds a mint key — not the #1203 gated-but-unmintable over-block: %r" % cmd
        )
        assert _execute(cmd, tmp_path) == _DENY, "gated row ALLOWED with no token: %r" % cmd
        minted, ctx = _mint(cmd, tmp_path)
        assert minted == 1, "OVER-BLOCK persists — implicit force-push gates but does NOT mint: %r" % cmd
        assert ctx.get("operation_type") == "force-push", "wrong op: %r" % ctx.get("operation_type")
        assert _won_key(ctx) == "force_push_implicit", (
            "implicit force-push bound the wrong key: %r (expected force_push_implicit): %r"
            % (_won_key(ctx), cmd)
        )
        assert ctx.get("force_push_implicit") == FP_IMPLICIT_VALUE[cmd], (
            "wrong force_push_implicit identity: got %r, expected %r (cycle-3 remote-qualification) for %r"
            % (ctx.get("force_push_implicit"), FP_IMPLICIT_VALUE[cmd], cmd)
        )
        assert _execute(cmd, tmp_path) == _ALLOW, "faithful click's own token did not self-authorize: %r" % cmd


class TestDimAMintMerge:
    """Bare `gh pr merge` / `--admin` / `--squash`: same shape on merge_implicit."""

    @pytest.mark.parametrize("cmd", MUST_MINT_MERGE, ids=MUST_MINT_MERGE)
    def test_mints_merge_implicit(self, cmd, tmp_path):
        assert D(cmd) is True, "bare merge not gated at head: %r" % cmd
        assert _base()(cmd) is True, "bare merge not dangerous at base: %r" % cmd
        assert _mint_keys(_base_ctx()(cmd)) == [], "base already mintable — not the over-block: %r" % cmd
        assert _execute(cmd, tmp_path) == _DENY, "gated bare merge ALLOWED with no token: %r" % cmd
        minted, ctx = _mint(cmd, tmp_path)
        assert minted == 1, "OVER-BLOCK persists — bare merge gates but does NOT mint: %r" % cmd
        assert ctx.get("operation_type") == "merge", "wrong op: %r" % ctx.get("operation_type")
        assert _won_key(ctx) == "merge_implicit", (
            "bare merge bound the wrong key: %r (expected merge_implicit): %r" % (_won_key(ctx), cmd)
        )
        assert ctx.get("merge_implicit") == MERGE_SENTINEL, "wrong sentinel value: %r" % ctx.get("merge_implicit")
        assert _execute(cmd, tmp_path) == _ALLOW, "faithful bare-merge token did not self-authorize: %r" % cmd


class TestDimAMintTargetNeuterKnownBad:
    """NON-VACUITY for minted==1: dropping force_push_implicit from _target_value (the exact
    #1064 four-site-omission shape) turns the gated implicit force-push gated-but-UNMINTABLE,
    so the faithful click is refused with no way to authorize. Demonstrates the minted==1
    assertions fail on a known-bad. `_target_value` referenced call-time via the module."""

    def test_dropping_sentinel_from_target_value_makes_it_unmintable(self, tmp_path, monkeypatch):
        row = _PG + "--force"
        vac = tmp_path / "vac"; vac.mkdir()
        minted, ctx = _mint(row, vac)
        assert minted == 1 and _won_key(ctx) == "force_push_implicit", "row does not mint the sentinel at head"

        def _tv_without_sentinel(cmd_ctx):
            return (cmd_ctx.get("pr_number") or cmd_ctx.get("branch")
                    or cmd_ctx.get("branch_set") or cmd_ctx.get("target_ref")
                    or cmd_ctx.get("push_set") or cmd_ctx.get("force_push_set")
                    or cmd_ctx.get("merge_implicit") or cmd_ctx.get("mass_target")
                    or cmd_ctx.get("protected_branch"))  # force_push_implicit DROPPED

        monkeypatch.setattr(mgpost, "_target_value", _tv_without_sentinel)
        kb = tmp_path / "kb"; kb.mkdir()
        m2, _ = _mint(row, kb)
        assert m2 == 0, "the sentinel-drop did not stop the mint — the four-site enumeration is inert"
        assert _execute(row, kb) == _DENY, (
            "gated-but-unmintable row was not denied — the mint pin cannot see the over-block it exists to catch"
        )


# ═════════════════════════════════════════════════════════════════════════════════
# (b) OFF-TOKEN ROUND-TRIP — identity read off the token; the exec-without-token DENY is
# the fix-coupled leg (the mint alone is pipeline-blind).
# ═════════════════════════════════════════════════════════════════════════════════
class TestDimBOffTokenRoundTrip:
    """The sentinel identity is read OFF the token (compound `cd && …` rows bind nothing on
    the whole-command extract, so the token file is authoritative). exec-without-token DENY
    proves the gate; mint+self-authorize ALLOW proves the round-trip."""

    @pytest.mark.parametrize("cmd", MUST_MINT_FORCE_PUSH + MUST_MINT_MERGE,
                             ids=MUST_MINT_FORCE_PUSH + MUST_MINT_MERGE)
    def test_identity_off_the_token(self, cmd, tmp_path):
        assert _execute(cmd, tmp_path) == _DENY, "gate not live at exec time (no token): %r" % cmd
        minted, ctx = _mint(cmd, tmp_path)
        assert minted == 1, "did not mint: %r" % cmd
        sentinel = ctx.get("force_push_implicit") or ctx.get("merge_implicit")
        # force_push_implicit may be the plain sentinel (truly-implicit 0-positional) OR a
        # cycle-4 netstring-qualified `SENTINEL + <_canonical_join([positional])>` (1-positional).
        # merge_implicit is the plain merge sentinel. Either way the identity is read OFF the
        # token as a NUL-framed sentinel (every force-push value begins with FP_SENTINEL).
        assert sentinel is not None and (
            sentinel == MERGE_SENTINEL or sentinel.startswith(FP_SENTINEL)
        ), "token did not carry a target-blind sentinel off the token file: %r (%r)" % (cmd, sentinel)
        assert _execute(cmd, tmp_path) == _ALLOW, "own token did not authorize: %r" % cmd

    def test_mint_plus_allow_is_pipeline_blind_without_the_gate(self, tmp_path, monkeypatch):
        # DOCUMENTED TRAP: under a sentinel-populate revert the row goes UNMINTABLE, but a
        # naive "mint + self-authorize" check that skipped the exec-WITHOUT-token DENY could
        # be fooled by a fix-independent mint path. Here we show the exec-without-token DENY
        # is the load-bearing leg: with the sentinel dropped, the row cannot mint AND cannot
        # authorize -> DENY. (Mirrors the 1134 cert's pipeline-blindness note.)
        row = _PG + "--force"
        # cycle-3 renamed the implicit-force-push predicate to the identity function
        # `_implicit_force_push_identity` (str|None). Neuter it to None (no identity) → the row
        # binds no force_push_implicit → gated-but-unmintable.
        monkeypatch.setattr(mgc, "_implicit_force_push_identity", lambda c: None)
        assert _mint_keys(mgc.extract_command_context(row)) == [], "revert did not un-mint the row (vacuity guard)"
        minted, _ = _mint(row, tmp_path)
        assert minted == 0, "row still minted after the populate revert"
        assert _execute(row, tmp_path) == _DENY, "unmintable row was not denied at exec"


# ═════════════════════════════════════════════════════════════════════════════════
# (c) NO-CROSS-AUTH — the sentinel is target-blind but STRUCTURALLY walled off explicit
# and cross-op forms (distinct NUL key + op-type-first + mutual-exclusive populate).
# ═════════════════════════════════════════════════════════════════════════════════
# (name, approve, execute, expect_allow)
CROSS_AUTH_ROWS = [
    ("fp-implicit -> explicit --force origin main", _PG + "--force", _PG + "--force origin main", False),
    ("fp-implicit -> explicit -f origin main", _PG + "--force", _PG + "-f origin main", False),
    ("explicit --force origin main -> fp-implicit", _PG + "--force origin main", _PG + "--force", False),
    ("merge-implicit -> explicit merge 42", _GH + "merge", _GH + "merge 42", False),
    ("explicit merge 42 -> merge-implicit", _GH + "merge 42", _GH + "merge", False),
    ("fp-implicit -> bare merge (cross-op)", _PG + "--force", _GH + "merge", False),
    ("merge-implicit -> fp-implicit (cross-op)", _GH + "merge", _PG + "--force", False),
    # faithful self-authorize (no over-block)
    ("fp-implicit self", _PG + "--force", _PG + "--force", True),
    ("merge-implicit self", _GH + "merge", _GH + "merge", True),
]


class TestDimCNoCrossAuth:
    """Every escalation REFUSES; every faithful self-execution AUTHORIZES. minted==1 asserted
    FIRST (via the owned-dir helper) so a DENY is a READ decision, never a mint-side miss."""

    @pytest.mark.parametrize("name,approve,execute,expect_allow", CROSS_AUTH_ROWS,
                             ids=[r[0] for r in CROSS_AUTH_ROWS])
    def test_cross_auth(self, name, approve, execute, expect_allow):
        _, rc = _isolated_roundtrip(approve, execute)
        if expect_allow:
            assert rc == _ALLOW, "OVER-BLOCK: a faithful self-execution was REFUSED (%s)" % name
        else:
            assert rc == _DENY, (
                "CROSS-AUTH OPEN: an implicit-form token authorized a DIFFERENT-identity "
                "execution (%s): approve=%r execute=%r" % (name, approve, execute)
            )

    def test_runtime_resolve_would_open_the_launder_known_bad(self, tmp_path, monkeypatch):
        # THE DISCRIMINATING KNOWN-BAD. Simulate the REJECTED runtime-resolve design (KD-1
        # option b): make the implicit force-push resolve to a CONCRETE target_ref='main'
        # (as `push.default=current` on branch main would) INSTEAD of the target-blind
        # sentinel. Now the implicit token carries target_ref='main' and cross-authorizes an
        # explicit `--force origin main` (target_ref='main') — the exact launder the target-
        # blind distinct-key design prevents. Patched on the POST hook's own binding (mint side).
        orig = mgpost.extract_command_context

        def _resolved(cmd, *a, **k):
            ctx = dict(orig(cmd, *a, **k))
            if ctx.get("force_push_implicit"):
                ctx.pop("force_push_implicit")
                ctx["target_ref"] = "main"    # runtime-resolve to the current branch
            return ctx

        monkeypatch.setattr(mgpost, "extract_command_context", _resolved)
        minted, ctx = _mint(_PG + "--force", tmp_path)
        assert minted == 1 and ctx.get("target_ref") == "main", (
            "the runtime-resolve simulation did not bind target_ref='main' (known-bad inert)"
        )
        # The implicit token now AUTHORIZES the explicit push to main — cross-auth OPENED.
        assert _execute(_PG + "--force origin main", tmp_path) == _ALLOW, (
            "the runtime-resolve known-bad did NOT open the cross-auth — the distinct target-"
            "blind key is not the attributable closer, so the DENY rows above prove nothing"
        )


class TestDimCMaxUsesResidual:
    """ACCEPTED RESIDUAL (KD-3): the target-blind sentinel + the existing max_uses=2 lifecycle
    means an implicit force-push token can be used a SECOND time — and because the sentinel is
    target-blind, that 2nd use may be a DIFFERENT current branch (another implicit form). This
    is ALLOWED and documented (target-precision under-block accepted per the over-block-priority
    directive), NOT a re-prompt. If MAX_USES were tightened to 1, the 2nd use would DENY — so
    this row also pins the current max_uses=2 lifecycle."""

    def test_max_uses_is_two(self):
        assert mgc.MAX_USES == 2, "MAX_USES changed — the KD-3 accepted-residual reasoning shifts"

    def test_second_use_rides_to_a_different_implicit_force_push(self, tmp_path):
        minted, _ = _mint(_PG + "--force", tmp_path)
        assert minted == 1
        assert _execute(_PG + "--force", tmp_path) == _ALLOW, "1st use denied (unexpected)"
        # 2nd use — a DIFFERENT implicit force-push spelling (target-blind => same sentinel).
        assert _execute(_PG + "-f", tmp_path) == _ALLOW, (
            "the max_uses=2 cross-branch 2nd-use ride is CLOSED — if this is intended (max_uses "
            "tightened), update the KD-3 accepted-residual documentation rather than this pin"
        )
        # 3rd use exhausts the 2 slots.
        assert _execute(_PG + "--force", tmp_path) == _DENY, "token outlived its max_uses=2 budget"


# ═════════════════════════════════════════════════════════════════════════════════
# (d) INERT-NOT-GATED — MUST_STAY_INERT + the C1b short-alias matrix.
# ═════════════════════════════════════════════════════════════════════════════════
class TestDimDInert:
    """Inert help forms un-gate at HEAD (merge/push nothing); GATED at 5017d1f2 base (no inert
    recognizer) — the base is the demonstrated known-bad."""

    @pytest.mark.parametrize("cmd", MUST_STAY_INERT, ids=MUST_STAY_INERT)
    def test_inert_ungated_at_head_gated_at_base(self, cmd):
        assert D(cmd) is False, "inert help form GATED at head (over-block): %r" % cmd
        assert DETECT(cmd) is None or D(cmd) is False  # help form contributes no live danger
        assert _base()(cmd) is True, (
            "inert form was NOT gated at 5017d1f2 base — the C1 inert-recognition differential "
            "is vacuous for this row (base==head): %r" % cmd
        )


class TestDimDC1bShortAlias:
    """The C1b short-alias value-decoy matrix: every `<value-flag> -h`/`--help` GATES at head
    (the -h/--help is the flag's VALUE => a real destructive command), for both short and long
    forms, merge AND close. Boolean `-h` stays inert. The C1b under-block was introduced by C1
    and closed by C1b WITHIN the arc, so it does NOT show against the 5017d1f2 base — its
    non-vacuity is the SUPPLEMENT-REVERT known-bad below, not a base differential."""

    @pytest.mark.parametrize("cmd,label", C1B_MUST_GATE, ids=[r[1] for r in C1B_MUST_GATE])
    def test_value_decoy_gates(self, cmd, label):
        assert D(cmd) is True, "C1b value-decoy did NOT gate (under-block): %s -> %r" % (label, cmd)

    @pytest.mark.parametrize("cmd", C1B_MUST_STAY_INERT, ids=C1B_MUST_STAY_INERT)
    def test_boolean_short_h_stays_inert(self, cmd):
        assert D(cmd) is False, (
            "a boolean short's following -h was treated as a VALUE and gated (over-block) — the "
            "boolean must NOT be in the skip set: %r" % cmd
        )

    def test_supplement_revert_reopens_the_short_alias_under_block(self, monkeypatch):
        # NON-VACUITY: neuter the C1b supplement (empty _INERT_HELP_EXTRA_VALUE_FLAGS). The
        # short-form value-decoys must UN-GATE again (the C1b under-block reopens), while the
        # LONG forms stay gated (they use the shared _GH_PR_VALUE_TAKING_FLAGS, untouched). This
        # is what couples the short-alias rows above to C1b rather than being a dead assertion.
        assert set(_C1B_MERGE_VALUE_SHORTS + _C1B_CLOSE_VALUE_SHORTS) <= set(mgc._INERT_HELP_EXTRA_VALUE_FLAGS), (
            "the short aliases are not in _INERT_HELP_EXTRA_VALUE_FLAGS — the revert below is a no-op"
        )
        monkeypatch.setattr(mgc, "_INERT_HELP_EXTRA_VALUE_FLAGS", frozenset())
        reopened = [cmd for cmd in (_GH + "merge 5 -t -h", _GH + "merge 5 -R -h",
                                    _GH + "close 5 -d -c -h") if D(cmd) is False]
        assert len(reopened) == 3, (
            "emptying the C1b supplement did NOT reopen the short-alias under-block on all 3 "
            "witnesses — the supplement is not the attributable closer: still-gated=%r"
            % [c for c in (_GH + "merge 5 -t -h", _GH + "merge 5 -R -h", _GH + "close 5 -d -c -h")
               if D(c) is True]
        )
        # LONG forms are NOT affected by the supplement (shared long set) — they still gate.
        assert D(_GH + "merge 5 --subject -h") is True, (
            "the long-form value skip regressed under the supplement revert — the two skip sets "
            "are not independent as documented"
        )


# ═════════════════════════════════════════════════════════════════════════════════
# (e) CONTROL-UNCHANGED — explicit ref'd forms + numbered merge byte-identical base->HEAD.
# ═════════════════════════════════════════════════════════════════════════════════
class TestDimEControlUnchanged:
    """The fix is a pure additive elif on the shared-None of the scalar/set extractors; the
    explicit ref'd forms and the numbered merge must be IDENTICAL base->HEAD (same gate, same
    op, same bound identity). A control whose identity changed would mean the fix perturbed the
    explicit path — a regression the additive design forbids."""

    @pytest.mark.parametrize("cmd,key,value", CONTROL_UNCHANGED, ids=[r[0] for r in CONTROL_UNCHANGED])
    def test_explicit_forms_identical_base_to_head(self, cmd, key, value):
        assert _base()(cmd) is True and D(cmd) is True, "control lost its gate base/head: %r" % cmd
        head_ctx = mgc.extract_command_context(cmd)
        base_ctx = _base_ctx()(cmd)
        assert head_ctx.get(key) == value, "head control identity changed: %r -> %r" % (cmd, head_ctx.get(key))
        assert base_ctx.get(key) == value, "base control identity differs: %r -> %r" % (cmd, base_ctx.get(key))
        # No sentinel ever leaks onto an explicit form (KD-4 mutual-exclusivity).
        assert not head_ctx.get("force_push_implicit") and not head_ctx.get("merge_implicit"), (
            "an EXPLICIT form carries a sentinel at head — the mutual-exclusive populate broke: %r" % cmd
        )

    def test_scalar_and_set_extractors_byte_untouched(self):
        # The scalar/set force-push extractors are byte-identical base->HEAD (the fix is a pure
        # additive elif on their shared None).
        base = load_baseline_5017d1f2()
        for cmd, key, value in CONTROL_UNCHANGED:
            if key == "target_ref":
                assert base._extract_force_push_target_ref(cmd) == mgc._extract_force_push_target_ref(cmd) == value, (
                    "scalar force-push extractor changed base->HEAD for %r" % cmd
                )

    def test_api_endpoint_merge_binds_pr_number_not_the_sentinel(self):
        # The #1096 API merge (`gh api -X PUT …/pulls/<N>/merge`) carries a concrete PR number,
        # so pr_number is bound and merge_implicit is NEVER populated (KD-4 mutual-exclusivity:
        # the bare-merge sentinel is reached ONLY when pr_number is absent). Byte-identical
        # base->HEAD — the fix did not touch the API-merge path.
        cmd = "gh " + "api -X PUT repos/o/r/pulls/5/merge"
        base = load_baseline_5017d1f2()
        head_ctx = mgc.extract_command_context(cmd)
        assert D(cmd) is True and head_ctx.get("operation_type") == "merge"
        assert head_ctx.get("pr_number") == "5", "API merge did not bind pr_number: %r" % head_ctx.get("pr_number")
        assert not head_ctx.get("merge_implicit"), (
            "the merge_implicit sentinel LEAKED onto a numbered API merge — KD-4 mutual-"
            "exclusivity broke (a bare-merge token could then authorize a numbered merge)"
        )
        assert base.extract_command_context(cmd).get("pr_number") == "5", "API merge pr_number changed base->HEAD"


# ═════════════════════════════════════════════════════════════════════════════════
# ACCEPTED RESIDUAL — SET B non-runnable ref-positional force-push: gated-but-unmintable,
# documented (NOT a defect). A "mint-on-no-target" fix would harmlessly over-mint them too;
# we pin the actual disposition and document the alternative as acceptable.
# ═════════════════════════════════════════════════════════════════════════════════
class TestSetBNonRunnableResidual:
    @pytest.mark.parametrize("cmd", DONT_CARE_RESIDUAL, ids=DONT_CARE_RESIDUAL)
    def test_set_b_gated_but_unmintable(self, cmd, tmp_path):
        # Gated (is_dangerous True) but binds no sentinel (SET B is excluded from the implicit
        # predicate — non-runnable, fatal 128, no faithful click to protect). Either "still
        # gated-but-unmintable" (pinned here) OR "harmlessly over-minted" is acceptable; a
        # regression that makes it MINT a force_push_implicit is fine, but one that UN-GATES it
        # would be an under-block — so we pin the gate stays True and the key stays a sentinel
        # (or absent), never an explicit target_ref.
        assert D(cmd) is True, "SET B non-runnable form UN-GATED (under-block): %r" % cmd
        ctx = mgc.extract_command_context(cmd)
        assert not ctx.get("target_ref") and not ctx.get("force_push_set"), (
            "SET B bound an EXPLICIT target — a non-runnable form must never resolve a concrete "
            "ref (that would be the launder the SET A/SET B split exists to avoid): %r" % cmd
        )


# ═════════════════════════════════════════════════════════════════════════════════
# M1 (peer-review TIGHTEN) — pr merge <branch>/<url> bind DISTINCT qualified targets;
# merge_implicit fires ONLY truly-bare. C3b's sentinel OVER-fired on a branch/url
# positional (an over-mint the INDEPENDENT security lane caught — the #70 cert missed
# this positional-type axis, mirroring the close url/branch axis the 1134 cert originally
# missed). The merge target binds under pr_number with a `_classify_pr_target`-qualified
# value (branch:<name> | url:<host>/<owner>/<repo>#<N> | <N>), the SAME shared SSOT close
# uses. --match-head-commit is a REAL gh pr merge value flag (derived-SSOT set): its value
# MUST be stripped so it never abstains (over-block) nor mis-binds a numeric value
# (laundering) — the security-gap regression guard against RE-NARROWING the merge walk (a
# hand-narrowed "merge-specific" subset dropped it once; the derived-SSOT superset is safe).
# ═════════════════════════════════════════════════════════════════════════════════
_URL5 = "https://github.com/o/r/pull/5"
# (cmd_tail, expected pr_number qualified value) — positional-bearing forms.
MERGE_TARGET = [
    ("feature", "branch:feature"),
    (_URL5, "url:github.com/o/r#5"),
    ("42", "42"),
    ("--subject foo bar", "branch:bar"),                     # value `foo` stripped -> `bar`
    ("--match-head-commit abc123 feature", "branch:feature"),  # SECURITY: value stripped
]
# truly-bare forms (0 positionals after the value-flag walk) -> merge_implicit sentinel.
MERGE_BARE = ["", "--admin", "--squash", "--subject foo", "--match-head-commit 123"]

# M1 injectivity / cross-auth (the qualified identity keeps distinct targets from cross-
# authorizing). (name, approve, execute, expect_allow).
MERGE_CROSS_AUTH = [
    ("branch-a != branch-b", _GH + "merge feature", _GH + "merge other", False),
    ("branch != number", _GH + "merge feature", _GH + "merge 42", False),
    ("merge_implicit != branch", _GH + "merge", _GH + "merge feature", False),
    ("url cross-repo", _GH + "merge " + _URL5, _GH + "merge https://github.com/o/other/pull/5", False),
    ("url cross-host", _GH + "merge " + _URL5, _GH + "merge https://ghe.evil.com/o/r/pull/5", False),
    ("merge branch != close branch (cross-op)", _GH + "merge feature", _GH + "close feature -d", False),
    ("faithful branch self", _GH + "merge feature", _GH + "merge feature", True),
]


class TestDimM1MergeTargetDistinct:
    """Every faithful pr-merge with a positional binds its DISTINCT qualified target under
    pr_number (never the bare sentinel); merge_implicit fires ONLY truly-bare. Closes the
    C3b over-mint (merge_implicit on a branch/url positional)."""

    @pytest.mark.parametrize("tail,value", MERGE_TARGET, ids=[r[0] for r in MERGE_TARGET])
    def test_positional_binds_qualified_not_sentinel(self, tail, value):
        cmd = (_GH + "merge " + tail).rstrip()
        c = mgc.extract_command_context(cmd)
        assert D(cmd) is True and c.get("operation_type") == "merge", "merge not gated: %r" % cmd
        assert c.get("pr_number") == value, (
            "merge target mis-bound: got %r expected %r for %r" % (c.get("pr_number"), value, cmd)
        )
        assert not c.get("merge_implicit"), (
            "merge_implicit OVER-fired on a positional target (the C3b over-mint M1 closed): %r" % cmd
        )

    @pytest.mark.parametrize("tail", MERGE_BARE, ids=[t or "(bare)" for t in MERGE_BARE])
    def test_truly_bare_binds_merge_implicit(self, tail):
        cmd = (_GH + "merge " + tail).rstrip()
        c = mgc.extract_command_context(cmd)
        assert c.get("merge_implicit") == MERGE_SENTINEL, "truly-bare merge did not mint merge_implicit: %r" % cmd
        assert not c.get("pr_number"), "truly-bare merge mis-bound a pr_number %r: %r" % (c.get("pr_number"), cmd)

    def test_match_head_commit_in_derived_value_flag_set(self):
        # SECURITY-GAP REGRESSION GUARD (against the #68 re-narrowing error): --match-head-commit
        # MUST be a recognized merge value flag or its value leaks as a positional -> over-block
        # AND numeric-value laundering. The derived-SSOT superset must be retained.
        assert "--match-head-commit" in mgc._GH_MERGE_VALUE_LONG, (
            "--match-head-commit dropped from the merge value-flag set — re-narrowing reopens the "
            "over-block (`--match-head-commit <sha> feature` abstains) + laundering "
            "(`--match-head-commit 123` mis-binds pr_number=123)"
        )

    def test_dropping_match_head_commit_reopens_overblock_and_launder(self, monkeypatch):
        # NON-VACUITY for the regression guard: exclude --match-head-commit from the merge walk
        # (the #68 error). The flag's value then leaks as a positional -> (a) over-block: `<sha>
        # feature` = 2 positionals -> abstain (no target, no sentinel); (b) laundering: `123` mis-
        # bound as pr_number. Demonstrates the guard fails on the known-bad.
        assert "--match-head-commit" in mgc._GH_MERGE_VALUE_LONG, "vacuity guard: flag not in set"
        narrowed = frozenset(mgc._GH_MERGE_VALUE_LONG) - {"--match-head-commit"}
        monkeypatch.setattr(mgc, "_GH_MERGE_VALUE_LONG", narrowed)
        over = mgc.extract_command_context(_GH + "merge --match-head-commit abc123 feature")
        assert not over.get("pr_number") and not over.get("merge_implicit"), (
            "dropping --match-head-commit did NOT reopen the over-block abstain — the guard proves nothing"
        )
        laund = mgc.extract_command_context(_GH + "merge --match-head-commit 123")
        assert laund.get("pr_number") == "123", (
            "dropping --match-head-commit did NOT reopen the numeric-value laundering mis-bind"
        )

    @pytest.mark.parametrize("name,approve,execute,expect_allow", MERGE_CROSS_AUTH,
                             ids=[r[0] for r in MERGE_CROSS_AUTH])
    def test_merge_target_cross_auth(self, name, approve, execute, expect_allow):
        _, rc = _isolated_roundtrip(approve, execute)
        if expect_allow:
            assert rc == _ALLOW, "OVER-BLOCK: faithful merge self-execution REFUSED (%s)" % name
        else:
            assert rc == _DENY, (
                "CROSS-AUTH OPEN: a merge token authorized a DIFFERENT-target execution (%s): "
                "approve=%r execute=%r" % (name, approve, execute)
            )

    def test_neuter_positional_walk_reopens_the_over_mint(self, monkeypatch):
        # NON-VACUITY (intra-arc-born tighten -> in-cert neuter, NOT a 5017 base differential): the
        # M1 tighten IS the `_gh_merge_positionals` walk, SHARED by _extract_merge_target AND
        # _is_bare_cli_merge (one boundary). Neuter it to the C3b behavior (0 positionals always) ->
        # `merge feature` binds merge_implicit again (the over-mint), so a bare-merge token
        # authorizes it cross-target. At HEAD the branch:feature identity REFUSES it.
        monkeypatch.setattr(mgc, "_gh_merge_positionals", lambda tokens: [])
        c = mgc.extract_command_context(_GH + "merge feature")
        assert c.get("merge_implicit") == MERGE_SENTINEL and not c.get("pr_number"), (
            "the positional-walk neuter did not revert `merge feature` to the over-mint sentinel — "
            "vacuity guard (the walk is not the shared boundary the cross-auth rows depend on)"
        )
        _, rc = _isolated_roundtrip(_GH + "merge", _GH + "merge feature")
        assert rc == _ALLOW, (
            "with the merge positional walk neutered, a bare-merge token did NOT authorize `merge "
            "feature` — the walk is not the attributable closer of the C3b over-mint, so the merge "
            "cross-auth rows above prove nothing"
        )


# ═════════════════════════════════════════════════════════════════════════════════
# F1 (from the #77 cert review) — DRIFT-PROOFING the force-push cross-auth boundary that
# is closed-by-construction (op-type-first + distinct NUL key) but was UNPINNED: the
# multi-ref force_push_set / push_set interaction and the max_uses=2 second-use × cross-op
# refusal. All verified refuse-by-construction; pinned so a future change to the
# force_push_set/push_set arms (#1195) or the max_uses logic cannot silently reopen a
# cross-auth without a red cert.
# ═════════════════════════════════════════════════════════════════════════════════
F1_CROSS_AUTH = [
    ("fp-implicit -> multi-ref force_push_set", _PG + "--force", _PG + "--force origin main next", False),
    ("multi-ref force_push_set -> fp-implicit", _PG + "--force origin main next", _PG + "--force", False),
    ("fp-implicit -> push-to-main (cross-op)", _PG + "--force", _PG + "origin main", False),
    ("push-to-main -> fp-implicit (cross-op)", _PG + "origin main", _PG + "--force", False),
]


class TestF1ForcePushCrossAuthDriftProof:
    """The force_push_implicit sentinel cannot cross-authorize a MULTI-ref force_push_set /
    push_set command, nor a cross-op push-to-main — closed by op-type-first + the distinct NUL
    key. Pinned here (unpinned in #70) to catch a future set-arm or op-type change."""

    @pytest.mark.parametrize("name,approve,execute,expect_allow", F1_CROSS_AUTH,
                             ids=[r[0] for r in F1_CROSS_AUTH])
    def test_boundary_refuses(self, name, approve, execute, expect_allow):
        _, rc = _isolated_roundtrip(approve, execute)
        assert rc == _DENY, (
            "F1 CROSS-AUTH OPEN (%s): approve=%r execute=%r — the force_push_implicit sentinel "
            "reached a multi-ref/cross-op command" % (name, approve, execute)
        )

    def test_max_uses_second_use_refuses_cross_op(self, tmp_path):
        # The accepted max_uses=2 cross-branch ride is SAME-OP only. A SECOND use on a CROSS-OP
        # command must still DENY (op-type-first applies per use).
        minted, _ = _mint(_PG + "--force", tmp_path)
        assert minted == 1
        assert _execute(_PG + "--force", tmp_path) == _ALLOW, "slot-1 same-op self denied (unexpected)"
        assert _execute(_GH + "merge 42", tmp_path) == _DENY, (
            "the max_uses=2 SECOND use authorized a CROSS-OP execution — op-type-first is not "
            "applied per-use"
        )


# ═════════════════════════════════════════════════════════════════════════════════
# CLOSE-OUTPUT-BEHAVIOR pin (per the lead's ruling on the _classify_pr_target refactor):
# _extract_close_target's classification tail moved into the shared `_classify_pr_target`
# SSOT (now shared by close AND merge). Close OUTPUT must be BYTE-IDENTICAL base(5017)->HEAD
# — an OUTPUT-behavior pin (there is no body-hash pin to evolve; this deliberately pins
# behavior, not source shape, so future close/merge classifier evolution stays output-safe).
# ═════════════════════════════════════════════════════════════════════════════════
CLOSE_OUTPUT_CORPUS = [
    _GH + "close 5 -d",
    _GH + "close feature -d",
    _GH + "close https://github.com/o/r/pull/9 -d",
    _GH + "close --repo o/r feature -d",
]


class TestCloseOutputBehaviorUnchanged:
    @pytest.mark.parametrize("cmd", CLOSE_OUTPUT_CORPUS, ids=CLOSE_OUTPUT_CORPUS)
    def test_close_target_byte_identical_base_to_head(self, cmd):
        base = load_baseline_5017d1f2()
        assert base._extract_close_target(cmd) == mgc._extract_close_target(cmd), (
            "close target OUTPUT changed base->HEAD under the _classify_pr_target refactor: %r" % cmd
        )
        # And the classification tail IS the shared SSOT now (merge reuses it).
        assert mgc._extract_close_target(_GH + "close feature -d") == "branch:feature"


# ═════════════════════════════════════════════════════════════════════════════════
# FINDING #4 (Workflow-sweep, HIGH — inert-help VALUE-OPTION blindness, both arms). The
# `git help <sub>` head-form recognizer AND the `-h`/`--help` flag-loop in _is_inert_help_leg
# derived their subcommand/help position from a token stream that did NOT account for git's
# LEADING global VALUE-options (-C / --work-tree / --git-dir / --namespace). So a value
# literally "help" (head-form arm) or literally "-h"/"--help" (flag-loop arm) was mis-read as
# the help subcommand / a help flag, and a REAL destructive leg was blanked as inert. A CLEAN
# C1 regression (base 5017 gated True; the C1-introduced recognizer un-gated it). Fix @4e3b5235
# (both arms): is_git_gh_head + immediate-adjacency `raw[head+1]=="help"` (head-form), and a
# subcommand ANCHOR (first non-dash raw token after the head) guarding the -h/--help flag-loop
# (`… and i >= anchor`). Enumeration-FREE (structural anchor, NOT a git-option list). gh's -R
# is already covered by C1b's _INERT_HELP_EXTRA_VALUE_FLAGS — an ORTHOGONAL arm, untouched.
# ═════════════════════════════════════════════════════════════════════════════════
_GITX = "git "
_PUSHF = "push " + "--force origin main"           # -> "push --force origin main" (assembled, inert source)
_PUSHDEL = "push " + "origin --delete feature"     # -> "push origin --delete feature"
_BRDEL = "branch " + "-D feature"                  # -> "branch -D feature"

# 6 head-form + 4 flag-loop GIT forms — each a REAL destructive leg whose git-global-value-
# option's value ("help" or "-h"/"--help") was mis-read as inert. ALL MUST GATE at the fix.
F4_HEAD_FORM = [
    _GITX + "-C help " + _PUSHF,
    _GITX + "-C help " + _PUSHDEL,
    _GITX + "-C help " + _BRDEL,
    _GITX + "--work-tree help " + _PUSHF,
    _GITX + "--namespace help " + _PUSHF,
    _GITX + "--git-dir help " + _PUSHF,
]
F4_FLAG_LOOP = [
    _GITX + "-C -h " + _PUSHF,
    _GITX + "--work-tree -h " + _PUSHF,
    _GITX + "--git-dir --help " + _PUSHF,
    _GITX + "--namespace -h " + _BRDEL,
]
F4_GATE = F4_HEAD_FORM + F4_FLAG_LOOP
# gh -R -h pr merge — ALREADY gated via C1b's -R value-skip (the orthogonal arm); a stays-gated
# double-coverage control (NOT closed by the finding-#4 git-option fix).
F4_GH_R_CONTROL = "gh " + "-R -h " + "pr " + "merge"
# genuine inert help forms that MUST stay inert (no reopened over-block from the fix).
F4_INERT = [
    _GITX + "help " + "push",                        # genuine git help
    "gh " + "help " + "pr " + "merge",               # gh help head-form
    _GH + "merge --help", _PG + "--force --help",    # subcommand help
    _GITX + "push " + "-h",                          # genuine post-subcommand help
    _GITX + "-C /repo " + "push " + "-h",            # -C value /repo then post-subcommand -h
]
# C1b value-decoys (gh short/long value-flag value == -h) stay gated (orthogonal arm).
F4_DECOY_GATED = [_GH + "merge 5 --subject -h", _GH + "merge 5 -t -h", _GH + 'merge 5 --subject "x --help y"']
# ACCEPTED adversarial-only structurally-inherent residual (architect #84 + security #85 ruled
# ACCEPT): -C's non-dash value /repo stops the anchor early, so --work-tree's -h reads as help.
F4_RESIDUAL = _GITX + "-C /repo --work-tree -h " + _PUSHF


class TestFinding4ValueOptionInertBlindness:
    """The 10 GIT value-option forms RE-GATE at the fix (the durable positive regression-guard);
    gh -R orthogonal-stays-gated; genuine inert forms + C1b decoys unchanged; the double-value-
    option residual pinned as an ACCEPTED structurally-inherent adversarial-only residual."""

    @pytest.mark.parametrize("cmd", F4_GATE, ids=F4_GATE)
    def test_git_value_option_form_gates(self, cmd):
        # DURABLE REGRESSION GUARD: any future re-un-gating of these reds directly. base 5017
        # ALSO gates (True) — the C1-introduced recognizer regressed it; the fix RE-gates.
        assert D(cmd) is True, "FINDING #4 REGRESSION: a real destructive git value-option leg was blanked as inert: %r" % cmd
        assert _base()(cmd) is True, "base 5017 does not gate this form (mislabeled): %r" % cmd

    def test_gh_dash_r_stays_gated_c1b_orthogonal(self):
        assert D(F4_GH_R_CONTROL) is True, "gh -R -h pr merge un-gated — C1b's orthogonal -R skip regressed"

    @pytest.mark.parametrize("cmd", F4_INERT, ids=F4_INERT)
    def test_inert_controls_stay_inert(self, cmd):
        assert D(cmd) is False, "FINDING #4 fix OVER-BLOCKED a genuine inert help form (cardinal): %r" % cmd

    @pytest.mark.parametrize("cmd", F4_DECOY_GATED, ids=F4_DECOY_GATED)
    def test_c1b_value_decoys_stay_gated(self, cmd):
        assert D(cmd) is True, "a C1b value-decoy un-gated under the finding-#4 fix: %r" % cmd

    def test_accepted_adversarial_residual_pinned(self):
        # PINNED-CURRENT-BEHAVIOR (NOT should-gate), modeled on the curl/wget WON'T-FIX +
        # force_push_implicit KD-3 accepted residuals. Architect #84 empirically proved closing
        # it (option b) breaks `git --no-pager push -h` (a cardinal over-block) or needs a
        # rot-prone git-global-option arity enumeration; both lanes ruled ACCEPT. If this form
        # ever CHANGES (gates, or the anchor shifts), this reds and forces a re-confirm of the
        # disposition rather than a silent shift.
        assert D(F4_RESIDUAL) is False, (
            "the ACCEPTED double-value-option residual changed behavior — re-confirm the "
            "architect(#84)/security(#85) disposition, do NOT silently update this pin: %r" % F4_RESIDUAL
        )

    def test_reverted_fix_reopens_all_10_git_forms(self, monkeypatch):
        # NON-VACUITY (mechanism a; intra-arc-born so a 5017 differential is vacuous —
        # base==PATCH==True). PROGRAMMATIC SOURCE-REVERT of BOTH fix symbols: take the LIVE
        # _is_inert_help_leg source, string-replace ONLY the two fix anchors, exec, monkeypatch.
        # The rev!=src + both-symbols-gone VACUITY GUARD means a future fix-shape change trips
        # this LOUDLY instead of going silently vacuous.
        import inspect
        src = inspect.getsource(mgc._is_inert_help_leg)
        rev = src.replace('tok in ("--help", "-h") and i >= anchor', 'tok in ("--help", "-h")')   # ARM 2
        rev = rev.replace('raw[head + 1] == "help"', '"help" in raw[head + 1:]')                    # ARM 1
        assert rev != src and "and i >= anchor" not in rev and 'raw[head + 1] == "help"' not in rev, (
            "the finding-#4 fix symbols were not found in _is_inert_help_leg's live source — the "
            "reverted-body neuter is INERT (the fix shape changed); re-derive the revert"
        )
        ns = dict(mgc.__dict__)
        exec(rev, ns)
        monkeypatch.setattr(mgc, "_is_inert_help_leg", ns["_is_inert_help_leg"])
        reopened = [c for c in F4_GATE if D(c) is False]
        assert len(reopened) == len(F4_GATE), (
            "reverting both fix symbols did NOT reopen all 10 GIT forms — the fix is not the "
            "attributable closer, so the gate rows prove nothing: still-gated=%r"
            % [c for c in F4_GATE if D(c) is True]
        )
        assert D(F4_GH_R_CONTROL) is True, (
            "gh -R -h un-gated under the finding-#4 revert — the revert touched C1b's orthogonal "
            "-R arm, not just the git-global-option anchors"
        )


# ═════════════════════════════════════════════════════════════════════════════════
# CYCLE-3 (findings #1+#2 TIGHTEN) — the force_push_implicit identity is now REMOTE-QUALIFIED
# for the 1-positional remote-only form. Before cycle-3, `git push --force origin` and
# `git push --force upstream` both minted the PLAIN sentinel, so a `--force origin` approval
# cross-authorized a `--force upstream` (a different remote) — a coarse-identity cross-auth.
# Cycle-3 binds `SENTINEL:<remote>` (via _implicit_force_push_identity, str|None), so the two
# are DISTINCT identities and cannot cross-authorize; the bare 0-positional forms keep the
# plain sentinel. The identity is remote-AGNOSTIC only in TOKEN ORDER (`--force origin` ==
# `origin --force`), never across DIFFERENT remotes. Mutual-exclusivity with explicit
# target_ref (`--force origin main`) and SET B (fpi None + gated) is preserved (no over-block).
# ═════════════════════════════════════════════════════════════════════════════════
# (cmd_tail, expected force_push_implicit identity)
CYCLE3_IDENTITY = [
    ("--force origin", _fpq("origin")),
    ("-f origin", _fpq("origin")),
    ("origin --force", _fpq("origin")),                  # ordering-agnostic (same identity)
    ("--force upstream", _fpq("upstream")),
    ("--force -o ci.skip origin", _fpq("origin")),       # -o value-skip preserved
    ("--force", FP_SENTINEL),                            # bare 0-positional keeps plain sentinel
]
# (name, approve, execute, expect_allow) — the cross-remote closure + the ordering-agnostic ALLOW.
CYCLE3_CROSS_AUTH = [
    ("--force origin != --force upstream (cross-remote CLOSED)", _PG + "--force origin", _PG + "--force upstream", False),
    ("bare --force != --force origin", _PG + "--force", _PG + "--force origin", False),
    ("--force origin == origin --force (ordering-agnostic)", _PG + "--force origin", _PG + "origin --force", True),
    ("--force origin self", _PG + "--force origin", _PG + "--force origin", True),
]


class TestCycle3RemoteQualifiedIdentity:
    """The 1-positional force-push mints a positional-QUALIFIED identity so distinct positionals
    cannot cross-authorize; token order is agnostic; the bare 0-positional keeps the plain
    sentinel. cycle-3 introduced `SENTINEL:<remote>`; cycle-4 (#90-93) generalized it to the
    injective NETSTRING `SENTINEL + _canonical_join([positional])` AND dropped the refspec-shape
    refusal, so ALL 1-positionals (named remotes, URL-remotes, refspec-shapes) now mint — closing
    the URL-remote FAITHFUL over-block (a URL is a valid runnable remote). The `_fpq()` helper
    derives the expected value through the REAL _canonical_join SSOT."""

    @pytest.mark.parametrize("tail,value", CYCLE3_IDENTITY, ids=[r[0] for r in CYCLE3_IDENTITY])
    def test_identity_value(self, tail, value):
        got = mgc.extract_command_context(_PG + tail).get("force_push_implicit")
        assert got == value, "cycle-3 identity mis-bound: got %r expected %r for %r" % (got, value, _PG + tail)

    @pytest.mark.parametrize("name,approve,execute,expect_allow", CYCLE3_CROSS_AUTH,
                             ids=[r[0] for r in CYCLE3_CROSS_AUTH])
    def test_cross_remote(self, name, approve, execute, expect_allow):
        _, rc = _isolated_roundtrip(approve, execute)
        if expect_allow:
            assert rc == _ALLOW, "OVER-BLOCK: a faithful same-identity force-push was REFUSED (%s)" % name
        else:
            assert rc == _DENY, (
                "CROSS-REMOTE CROSS-AUTH OPEN (%s): approve=%r execute=%r — the remote qualifier "
                "did not distinguish the identities" % (name, approve, execute)
            )

    def test_mutual_exclusivity_and_multiref_preserved(self):
        # explicit target_ref still WINS for a remote+ref pair (no implicit sentinel leak).
        ex = mgc.extract_command_context(_PG + "--force origin main")
        assert ex.get("target_ref") == "main" and not ex.get("force_push_implicit"), (
            "leaked an implicit sentinel onto an EXPLICIT remote+ref force-push: %r" % ex
        )
        # cycle-4: a 1-positional refspec-shape (`--force HEAD`) now MINTS the netstring identity
        # (the URL-remote closure binds ALL 1-positionals), NOT SET-B-None.
        hd = mgc.extract_command_context(_PG + "--force HEAD")
        assert hd.get("force_push_implicit") == _fpq("HEAD") and D(_PG + "--force HEAD") is True, (
            "cycle-4: `--force HEAD` must mint the netstring identity (1-positional), not stay SET-B None: %r" % hd
        )
        # multi-ref (>=2 refspecs) still owns force_push_set — the set path, never the implicit sentinel.
        mr = mgc.extract_command_context(_PG + "--force origin main next")
        assert mr.get("force_push_set") and not mr.get("force_push_implicit"), (
            "multi-ref force-push leaked the implicit sentinel — the set path must own >=2 refspecs: %r" % mr
        )

    def test_dropping_remote_qualifier_reopens_cross_remote(self, monkeypatch):
        # NON-VACUITY: coarsen the identity back to the PLAIN sentinel (the pre-cycle-3 shape) —
        # `_implicit_force_push_identity` returns SENTINEL for ANY implicit form, dropping the
        # `:<remote>` qualifier. Then `--force origin` and `--force upstream` share ONE identity
        # and cross-authorize (the cross-remote reopens). Couples the cross_remote rows to the
        # remote qualification.
        orig = mgc._implicit_force_push_identity
        # sanity: the live fn currently qualifies (else the coarsen below is a no-op).
        assert orig(_PG + "--force origin") == _fpq("origin"), "vacuity guard: identity not remote-qualified"
        monkeypatch.setattr(mgc, "_implicit_force_push_identity", lambda c: (FP_SENTINEL if orig(c) else None))
        _, rc = _isolated_roundtrip(_PG + "--force origin", _PG + "--force upstream")
        assert rc == _ALLOW, (
            "coarsening the identity to the plain sentinel did NOT reopen the cross-remote cross-"
            "auth — the remote qualifier is not the attributable closer, so the DENY rows prove nothing"
        )

    # cycle-4: URL-remote + refspec-shape 1-positionals now MINT (the netstring closure of the
    # URL-remote over-block). Identity is SENTINEL + _canonical_join([positional]), URL-verbatim.
    _URL_A = "git@github.com" + ":o/r.git"
    _URL_B = "git@github.com" + ":o/other.git"

    @pytest.mark.parametrize("url_remote", [
        "git@github.com" + ":o/r.git",           # scp-style
        "ssh://git@host" + "/o/r.git",           # ssh URL
        "https://github.com" + "/o/r.git",       # https URL
    ], ids=["scp", "ssh", "https"])
    def test_url_remote_mints_netstring_identity(self, url_remote):
        # BEHAVIOR FLIP (cycle-4, #90/#91/#92/#93): a URL-remote force-push (`git push --force
        # git@host:repo`) IS a FAITHFUL runnable command — a URL is a valid git remote — so its
        # former gated-but-UNMINTABLE state (the SET-B pin committed at b49af7ea) was a CARDINAL
        # over-block. The netstring redesign now MINTS it: force_push_implicit == SENTINEL +
        # _canonical_join([url]) (injective, URL captured VERBATIM), gated + self-authorizing.
        cmd = _PG + "--force " + url_remote
        c = mgc.extract_command_context(cmd)
        assert D(cmd) is True, "URL-remote force-push UN-GATED (would be an under-block): %r" % cmd
        assert c.get("force_push_implicit") == _fpq(url_remote), (
            "URL-remote did not mint the netstring identity: got %r expected %r for %r"
            % (c.get("force_push_implicit"), _fpq(url_remote), cmd)
        )
        _, rc = _isolated_roundtrip(cmd, cmd)
        assert rc == _ALLOW, "faithful URL-remote force-push did not self-authorize (over-block): %r" % cmd

    def test_url_and_refspec_injectivity_self_authorizing_only(self):
        # INJECTIVITY (the point of the netstring): every newly-minting 1-positional authorizes
        # ONLY itself. A URL token ⊥ a different URL / a named remote / the bare form; a refspec-
        # shape ⊥ every other form. (name, approve, execute, expect_allow.)
        rows = [
            ("URL-A != URL-B", _PG + "--force " + self._URL_A, _PG + "--force " + self._URL_B, False),
            ("bare != URL-A", _PG + "--force", _PG + "--force " + self._URL_A, False),
            ("named origin != URL-A", _PG + "--force origin", _PG + "--force " + self._URL_A, False),
            ("HEAD:main != +main", _PG + "--force HEAD:main", _PG + "--force +main", False),
            ("HEAD:main != origin", _PG + "--force HEAD:main", _PG + "--force origin", False),
            ("URL-A self", _PG + "--force " + self._URL_A, _PG + "--force " + self._URL_A, True),
            ("HEAD:main self", _PG + "--force HEAD:main", _PG + "--force HEAD:main", True),
        ]
        for name, approve, execute, expect_allow in rows:
            _, rc = _isolated_roundtrip(approve, execute)
            if expect_allow:
                assert rc == _ALLOW, "OVER-BLOCK: a faithful 1-positional self-execution REFUSED (%s)" % name
            else:
                assert rc == _DENY, (
                    "CROSS-AUTH OPEN (%s): a netstring 1-positional token authorized a DIFFERENT "
                    "1-positional — the netstring identity is not injective" % name
                )

    def test_reverting_url_refspec_binding_reopens_the_over_block(self, monkeypatch):
        # NON-VACUITY (cycle-4 revert-of-the-fix known-bad): cycle-4 DROPPED cycle-3's
        # `:`/`refs/`/`HEAD`/`+` single-positional REFUSAL (binding ALL 1-positionals via the
        # injective netstring) — that drop is what CLOSED the URL-remote over-block. Re-apply the
        # pre-cycle-4 refusal (refuse a `:`-bearing / refspec-shaped positional) → the URL +
        # refspec-shape forms mint None again = gated-but-UNMINTABLE = the over-block REOPENS.
        # Couples the URL/refspec mint rows to the cycle-4 drop-refusal; a plain named remote is
        # UNAFFECTED (the attributable closer is specifically the drop-refusal, not the netstring
        # reframe alone).
        orig = mgc._implicit_force_push_identity
        assert orig(_PG + "--force " + self._URL_A) is not None, (
            "vacuity guard: the live fn does not currently bind a URL — cycle-4 absent, neuter is a no-op"
        )

        def _pre_cycle4(cmd):
            ident = orig(cmd)
            if ident and ident != FP_SENTINEL:
                pos = ident[len(FP_SENTINEL):].split(":", 1)[1]   # netstring `<len>:<positional>`
                if ":" in pos or pos.startswith("refs/") or pos.startswith("+") or pos == "HEAD":
                    return None                                   # pre-cycle-4 refusal
            return ident

        monkeypatch.setattr(mgc, "_implicit_force_push_identity", _pre_cycle4)
        for pos in [self._URL_A, "HEAD:main", "+main", "HEAD"]:
            c = mgc.extract_command_context(_PG + "--force " + pos)
            assert not c.get("force_push_implicit"), (
                "reverting the URL/refspec binding did NOT reopen the over-block for %r — the "
                "cycle-4 drop-refusal is not the attributable closer, so the URL/refspec mint rows "
                "prove nothing" % pos
            )
        assert mgc.extract_command_context(_PG + "--force origin").get("force_push_implicit") == _fpq("origin"), (
            "the revert wrongly un-bound a plain named remote — the pre-cycle-4 neuter over-reached"
        )
