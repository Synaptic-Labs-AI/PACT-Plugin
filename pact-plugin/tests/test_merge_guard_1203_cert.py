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
        assert ctx.get("force_push_implicit") == FP_SENTINEL, "wrong sentinel value: %r" % ctx.get("force_push_implicit")
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
        assert sentinel in (FP_SENTINEL, MERGE_SENTINEL), (
            "token did not carry a target-blind sentinel off the token file: %r" % cmd
        )
        assert _execute(cmd, tmp_path) == _ALLOW, "own token did not authorize: %r" % cmd

    def test_mint_plus_allow_is_pipeline_blind_without_the_gate(self, tmp_path, monkeypatch):
        # DOCUMENTED TRAP: under a sentinel-populate revert the row goes UNMINTABLE, but a
        # naive "mint + self-authorize" check that skipped the exec-WITHOUT-token DENY could
        # be fooled by a fix-independent mint path. Here we show the exec-without-token DENY
        # is the load-bearing leg: with the sentinel dropped, the row cannot mint AND cannot
        # authorize -> DENY. (Mirrors the 1134 cert's pipeline-blindness note.)
        row = _PG + "--force"
        monkeypatch.setattr(mgc, "_is_implicit_current_branch_force_push", lambda c: False)
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
