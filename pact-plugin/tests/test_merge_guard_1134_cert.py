"""
Location: pact-plugin/tests/test_merge_guard_1134_cert.py
Summary: The DURABLE three-dimension certification for the non-first-leg destructive
         recognition fix (#1134) and the remote-delete PRIVILEGED_FLAGS split binding.
         Certifies against the REAL classifier + REAL post/pre hooks, base (committed
         vendored fixture at 172a77dd via merge_guard_baseline_loader — loud-fail,
         CI-executable, never skip) vs live HEAD. NEVER a byte-diff / git-show-by-SHA.

WHY THREE DIMENSIONS. is_dangerous_command is only ONE of the three surfaces this arc
moves. A monotonicity sweep that watches is_dangerous alone would go green while seeing
at most a third of the change — the same shape of error as the #1118 +N/-0 byte-diff
"certificate", one abstraction level up. The three surfaces, each with its own oracle:

  D1  is_dangerous_command       — leg-position parity (the gate)
  D2  mintability                — minted==1 + which-key-won (OFF THE TOKEN) + a REAL
                                   post_main -> pre_main round-trip (the gated-but-
                                   unmintable guard, the cardinal over-block's subtler half)
  D3  detect_command_operation_type — op-parity (mint==read: gate <=> detect non-None)

GOVERNING MODEL — SACROSANCT, GOOD-FAITH. The merge-guard routes an HONEST destructive
command through the user's approval click; a faithful single-command click always mints
and self-authorizes. The ONLY cardinal sin is the OVER-BLOCK: blocking a faithful click,
INCLUDING gating it but leaving it unmintable (gated-but-unmintable). Under-blocks are
tolerated ONLY when they take deliberate/adversarial construction a good-faith user would
never type; a good-faith DESTRUCTIVE command running ungated is STILL unacceptable (that
is exactly what #1134 was — `cd /repo && git push origin --delete feature` ran completely
ungated). MUST_HOLD is the over-block direction, the one with NO tolerance band.

STATED RESIDUAL (security §1 — carried VERBATIM, load-bearing not decorative):
    The by-construction subset argument bounds the CLASS of false positives, not the
    COUNT. If an unfound first-leg false positive F exists, the widening MULTIPLIES its
    reach across leg positions. Absence of F is precisely what a corpus cannot prove.
    The benign parity rows are the live guard against this.
Consequence for reading this file: a GREEN run is NOT a proof of the universal "no
faithful click is ever over-blocked". The BENIGN parity sweep and MUST_STAY_OFF rows
bound the CLASS of over-blocks we enumerated; they cannot bound the COUNT of first-leg
false positives that were never enumerated. Do not let the green cert read as more.

ADVERSARIAL-ONLY RESIDUALS (documented, NOT closed — do not re-file as over-blocks). Two
non-faithful close forms are gated/minted with a wrong-but-harmless identity, tolerated
under the good-faith model because gh itself REFUSES the command, so there is no faithful
click to protect:
  (1) NO-ARG close (`gh pr close -d`) — gh requires a positional, so it never runs (see the
      note by MUST_STAY_OFF). Gated-but-non-faithful.
  (2) UNKNOWN/misapplied value-flag with a numeric value and NO positional
      (`gh pr close --label 5 -d`) — the positional-first close fold cannot close this (an
      allow-list was rejected as a future over-block), so it still mints '5' BY DESIGN. It
      is a confused-deputy laundering vector reachable ONLY by an adversary crafting a
      non-runnable approval. The "still mints '5'" assertion is OWNED by the coder's
      test_unknown_value_flag_on_nonfaithful_close_is_adversarial_only (the SSOT); this cert
      REFERENCES it and does NOT duplicate it or assert abstention. FAITHFUL commands (a real
      value-flag WITH a positional) all mint the CORRECT identity — see
      TestCloseRepoValueMisbindClosed.

CORPUS AND ITS AXES (a probe is only as complete as its corpus — state it):
  * 53 differential rows: MUST_FLIP 22 / MUST_HOLD 29 / MUST_STAY_OFF 2, plus 8 BENIGN.
  * MUST_HOLD is the UNION of two independent enumerations that diverged by 13 rows on
    this exact class. It is NOT trimmed to the intersection — the over-block direction
    gets the superset.
  * Leg-position axis: 5 benign prefixes (`cd &&`, `cd ;`, `git status &&`, `echo &&`,
    `false ||`). Each is a benign-CONTINUATION carrier that must not change a verdict.
  * Close POSITIONAL-TYPE axis (number/url/branch x first-leg/non-first-leg): covered by
    TestCloseUrlBranchNowGateAndMint + TestCloseMintLaundering. This axis was originally
    MISSED — the corpus swept only the number positional, and a faithful gated-but-
    unmintable over-block hid in the url/branch forms until a base-vs-fixed sweep across
    the positional type surfaced it. The lesson (a probe is only as complete as its corpus)
    is why the axis is now enumerated explicitly.
  * NOT swept here (declared, deferred with analysis on the PR): >3 legs; quoted/nested
    subshells; non-git/gh carriers (curl/wget/httpie). See the PR's deferred table.

WHICH-KEY-WON IS READ OFF THE MINTED TOKEN, NEVER off extract_command_context(command).
On the compound rows #1134 is ABOUT (`cd /repo && git push origin --delete feature`),
the whole-command extractor binds ZERO identity keys, yet the faithful click still MINTS
because the mint path selects the destructive leg via _extraction_surface. A which-key-won
assertion read from the whole-command context would misreport on exactly those rows —
false-fail, or vacuously green if weakened to tolerate the empty binding. The authoritative
signal is the token's own `context` dict on disk.

THE MINT+ALLOW ROUND-TRIP ALONE IS PIPELINE-BLIND TO THE #1134 GATE (documented so it is
not "simplified" into a check that proves nothing). Measured: under an _PER_LEG_OPS revert
the newly-gated row goes UNGATED yet mint+exec still returns (1, ALLOW) — the mint uses the
fix-independent _extraction_surface, and the exec ALLOWs because the row is no longer gated,
not because the token authorized it. The assertions COUPLED to the fix are (a) exec-WITHOUT-
token -> DENY (proves the row is gated; base known-bad = ungated -> ALLOW) and (b) minted==1
paired with is_dangerous==True (the gated-but-unmintable guard; known-bad = a mint-target
neuter -> minted 0 -> DENY). See TestD2Mintability for both.

KNOWN-BAD DISCIPLINE. Every assertion here is demonstrated FAILING on a known-bad, because
this arc found FIVE checks that proved nothing and ONE invariant (the `-D\\b` word boundary)
with NO check at all. Where two mechanisms both cover a row (the per-leg filter AND a
literal arm, or the per-leg filter AND the raw-fallback detect arm), NEITHER endpoint
(all-on / all-off) can detect a silently-dead one — so the mechanism neuters ISOLATE a
single mechanism and carry a vacuity guard asserting the neuter is not a no-op.

TOKEN-DIR ISOLATION RULE (a standing rule — this class bit twice). Any test that mints
tokens in a LOOP over rows must give each iteration a FRESH token dir; a leaked token from
an earlier row contaminates a later row's authorize/refuse outcome. It first hit a D2
gated-but-unmintable known-bad, then RECURRED in the binding base-sim loop, where it flipped
`--no-verify` to a false "stays closed" and produced a wrong "doubly-defended" attribution
that a clean re-measurement overturned. Parametrized single-row tests are safe (pytest hands
each a fresh tmp_path); the hazard is a for-loop over rows inside ONE test. Route such loops
through `_isolated_roundtrip`, which OWNS its token dir and cannot share it by construction.

SEQUENCING — code-commit-then-tests does NOT apply here, and the stale lesson must not be
re-imported. That rule exists to avoid a red intermediate when tests assert against a HEAD
that does not yet contain the fix. This cert is a base-vs-HEAD DIFFERENTIAL whose base is a
COMMITTED VENDORED FIXTURE (172a77dd bytes), not a git checkout — it is CI-executable at any
HEAD without the pre-fix commit being reachable, and it cannot go red from commit ordering
because the pre-fix state is frozen in the fixture. (This file ABSORBS the scratchpad
verify_postfix.py verifier: a cert test runs in CI on every future change, forever; a
scratchpad script runs once, by whoever remembers.)

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
from merge_guard_baseline_loader import load_baseline_172a77dd  # noqa: E402
from merge_guard_post import main as post_main  # noqa: E402
from merge_guard_pre import main as pre_main  # noqa: E402

# Runtime-assembled command stems (kept out of one literal so this file is inert).
_PG = "git " + "push "
_GH = "gh " + "pr "
_GB = "git " + "branch "
_PREFIX = "cd /repo && "

D = mgc.is_dangerous_command
DETECT = mgc.detect_command_operation_type


def _base():
    """The pre-#1134 classifier's is_dangerous_command (loud-fail vendored fixture)."""
    return load_baseline_172a77dd().is_dangerous_command


def _base_detect():
    return load_baseline_172a77dd().detect_command_operation_type


# ═════════════════════════════════════════════════════════════════════════════════
# CORPUS — module-level data, the cert IS the corpus.
# MUST_FLIP rows carry (command, expected_op, expected_target_key) so D2/D3 can assert
# which-key-won and op-parity, not merely a boolean.
# ═════════════════════════════════════════════════════════════════════════════════
MUST_FLIP = [
    # branch-delete (6) — literal floor is only -D\b | --delete --force | --force --delete,
    # so these cluster/separated spellings are LEFT BEHIND in a non-first leg at base.
    (_GB + "-Df temp", "branch-delete", "branch"),
    (_GB + "-fD temp", "branch-delete", "branch"),
    (_GB + "--delete -f temp", "branch-delete", "branch"),
    (_GB + "-df temp", "branch-delete", "branch"),
    (_GB + "-fd temp", "branch-delete", "branch"),
    (_GB + "-d -f temp", "branch-delete", "branch"),
    # close (4)
    (_GH + "close 5 -d", "close", "pr_number"),
    (_GH + "close 5 -dR owner/repo", "close", "pr_number"),
    (_GH + "close 5 -d -R owner/repo", "close", "pr_number"),
    (_GH + "close 5 -R owner/repo -d", "close", "pr_number"),
    # remote-ref-delete (7) — WHOLLY left behind (no literal arm at all).
    (_PG + "origin :main", "remote-ref-delete", "target_ref"),
    (_PG + "origin --delete main", "remote-ref-delete", "target_ref"),
    (_PG + "-d origin main", "remote-ref-delete", "target_ref"),
    (_PG + "--delete origin main", "remote-ref-delete", "target_ref"),
    (_PG + "origin -d main", "remote-ref-delete", "target_ref"),
    (_PG + "-du origin main", "remote-ref-delete", "target_ref"),
    (_PG + "-ud origin main", "remote-ref-delete", "target_ref"),
    # remote-mass-delete (5) — WHOLLY left behind.
    (_PG + "--mirror origin", "remote-mass-delete", "mass_target"),
    (_PG + "--prune origin --all", "remote-mass-delete", "mass_target"),
    (_PG + "origin :a :b", "remote-mass-delete", "mass_target"),
    (_PG + "origin --delete a b", "remote-mass-delete", "mass_target"),
    (_PG + "-d origin a b", "remote-mass-delete", "mass_target"),
]

# The 4 close rows carry detect=close at BASE via the raw-fallback gh-pr arm (NOT the
# per-leg filter), while is_dangerous is False — a pre-fix detect/gate ASYMMETRY the fix
# closes. So on the DETECT side the per-leg filter SOLELY drives the 18 non-close rows;
# the D3 filter-neuter isolates on those. (This is the two-mechanism blindness, in D3.)
MUST_FLIP_FILTER_DRIVES_DETECT = [
    r for r in MUST_FLIP if r[1] != "close"
]
assert len(MUST_FLIP_FILTER_DRIVES_DETECT) == 18

# MUST_HOLD (29) — gated in BOTH positions at base AND head. They certify nothing on
# their own (they pass pre-fix); their job is to catch a future change that BREAKS
# working coverage. The over-block direction with no tolerance band; the UNION of two
# independent enumerations (not the intersection).
MUST_HOLD = [
    _GB + "-D temp", _GB + "-D a b", _GB + "-D -f temp",
    _GB + "-f -D temp", _GB + "--delete --force temp",
    _GB + "--force --delete temp", _GB + "-D --force temp",
    _GB + "--delete --force a b",
    _GH + "close 5 --delete-branch",
    _GH + "close 5 --delete-branch --repo owner/repo",
    _GH + "merge 42", _GH + "merge 42 --admin", _GH + "merge 42 -d",
    _GH + "merge 42 --admin -d", _GH + "merge 42 -dR owner/repo",
    _GH + "merge 42 --admin --delete-branch",
    _PG + "--force origin main", _PG + "-f origin main",
    _PG + "-fu origin main", _PG + "-uf origin main",
    _PG + "-f -u origin main", _PG + "--force -u origin main",
    _PG + "origin main", _PG + "origin HEAD:main",
    _PG + "origin main:main", _PG + "-u origin main",
    _GH + "merge 42 --squash --admin",
    _PG + "--force origin main feature",
    # op=push-to-main, NOT force-push: --force-with-lease is the SAFE exclusion on the
    # force arm, so this row's coverage comes from the push-to-main arm — a fix touching
    # push-to-main is what it guards.
    _PG + "--force-with-lease origin main",
]

# MUST_STAY_OFF (2) — NOT dangerous even bare, correctly. Gating these = OVER-BLOCK.
# Both are NUMBERED (a runnable close) and correctly ungated.
MUST_STAY_OFF = [
    _GH + "close 5",                 # reversible close, no delete flag
    _GH + "close 5 -Rd owner/repo",  # -R value-taking: -Rd == -R=d, no -d delete present
]
# NB the NO-ARG close forms (`gh pr close -d`, `gh pr close --delete-branch`, and their
# non-first-leg carriers) ARE gated at HEAD, but that is NOT an over-block and they are
# NEITHER MUST_FLIP NOR MUST_STAY_OFF: `gh pr close` REQUIRES an explicit target
# (`{<number>|<url>|<branch>}` — braces = required; it is `gh pr view` that infers the
# branch). A command the CLI refuses to run has no faithful click, so gating it costs no
# honest user anything — gated-but-non-faithful, harmless. Do not "fix" it to ungate, and
# do not add it as a MUST_STAY_OFF row: asserting it ungated would mandate loosening a
# harmless gate toward a real under-block. (The numberless-close gate is by-construction
# unmintable — no target — but that is moot precisely because no faithful click exists.)

# BENIGN (8) — the first-leg false-positive guard (the over-block direction). Chosen to
# stress the widening: benign pushes NOT to main, a filter-value that MENTIONS delete
# tokens, and the exact `-o "note: use --mirror ..."` shape that literal per-leg arms
# were measured to over-block (3 cardinal over-blocks vs the filter's 0).
BENIGN = [
    "git status",
    "git log --committer 'note: --delete and -D mentioned'",
    _PG + "origin feature",
    _GB + "feature-Dashboard",       # branch CREATION; the `-D\b` word-boundary guard
    _GH + "view 5",
    _GH + "list",
    _GB + "-a",
    _PG + "origin feature -o \"note: use --mirror for backups\"",
]

# Benign branch CREATIONS whose `-D\b` guard the boundary removal would break (they
# contain a mid-word `-D`: featur[e-D]ashboard). The dedicated \b known-bad set.
BENIGN_BRANCH_CREATION = [
    _GB + "feature-Dashboard",
    _GB + "my-Diagram-branch",
    _GB + "release-Docs",
]

# Benign-CONTINUATION leg carriers. is_dangerous(P + C) MUST equal is_dangerous(C).
PREFIXES = [
    ("cd &&", "cd /tmp/r && "),
    ("cd ;", "cd /tmp/r ; "),
    ("status &&", "git status && "),
    ("echo &&", "echo hi && "),
    ("||", "false || "),
]

# The 4 classes the #1134 widening ADDED to the per-leg filter (the fix itself).
NEW_PER_LEG_CLASSES = ("branch-delete", "close", "remote-ref-delete", "remote-mass-delete")

# The count pin: MUST_FLIP spellings x prefixes. Pinned so a corpus-size drift is loud.
COUNT_PIN_SPELLINGS = 22
COUNT_PIN_VIOLATIONS = COUNT_PIN_SPELLINGS * len(PREFIXES)  # 22 x 5 = 110

# MINT-PIN gap-closure (security review). The shipped scratchpad probe (probe_1134_mintpath)
# patched a FOUR-tuple that EXCLUDED `close` and `branch-delete`, so those two newly-reached
# classes were GATE-pinned only — never MINT-pinned. A gate with no verified mint path is
# the cardinal gated-but-unmintable over-block waiting to be introduced. These rows pin
# minted==1 + identity-off-the-token for close and branch-delete in a NON-FIRST leg, across
# TWO distinct benign carriers (so the pin is not an artifact of one prefix's parse), closing
# the exclusion explicitly. (cmd, expected_op, expected_target_key.)
MINT_PIN_CLOSE_AND_BRANCH_DELETE = [
    ("cd /repo && " + _GH + "close 5 -d", "close", "pr_number"),
    ("git fetch && " + _GH + "close 5 -dR owner/repo", "close", "pr_number"),
    ("cd /repo && " + _GB + "-Df temp", "branch-delete", "branch"),
    ("git fetch && " + _GB + "-D old-feature", "branch-delete", "branch"),
]

# ── Close POSITIONAL-TYPE axis (the axis the corpus originally MISSED) ─────────────
# `gh pr close` accepts {<number>|<url>|<branch>}. The corpus covered only the NUMBER
# positional, so a faithful gated-but-unmintable OVER-BLOCK hid in the url/branch forms:
# commit 1 gated the NON-FIRST-LEG forms, and the FIRST-LEG url/branch forms were a
# PRE-EXISTING gated-but-unmintable. The commit-5 extractor fix makes ALL faithful close
# forms gate+MINT, binding `pr_number` with a positional-type-QUALIFIED value:
#   number -> '5'  |  url -> 'url:<host>/<owner>/<repo>#<N>'  |  branch -> 'branch:<name>'
# Measured base(172a77dd) -> fixed(commit 5):
#   url/branch FL : base gated+mint0 (the over-block) -> fixed gate+mint   [D2 mint-flip]
#   url/branch NFL: base UNGATED                      -> fixed gate+mint   [D1 gate + D2 mint]
# (The number FL row is base gate+mint already — the control; number NFL is the #1134
# leg-position flip, already in MUST_FLIP.)
_URL = "https://github.com/o/r/pull/5"
# (cmd, expected_pr_number_value, base_gated)  base_gated True => D2 mint-flip; False => D1.
CLOSE_URL_BRANCH_FLIP = [
    (_GH + "close " + _URL + " -d", "url:github.com/o/r#5", True),           # url FL  (D2)
    (_PREFIX + _GH + "close " + _URL + " -d", "url:github.com/o/r#5", False),  # url NFL (D1+D2)
    (_GH + "close feature -d", "branch:feature", True),                      # branch FL (D2)
    (_PREFIX + _GH + "close feature -d", "branch:feature", False),            # branch NFL (D1+D2)
]

# Cross-spelling / cross-host / cross-repo LAUNDERING. The host-qualified close identity is
# what makes a url/branch token refuse a DIFFERENT-looking-but-same-number execution. Each
# escalation must REFUSE; each faithful self-execution must AUTHORIZE. minted==1 asserted
# FIRST so a DENY is a read decision. (name, approve, execute, expect_allow.)
_URL_ENT = "https://github.enterprise.com/o/r/pull/5"
_URL_OTHER = "https://github.com/o/other/pull/5"
CLOSE_LAUNDERING = [
    ("url token -> bare number", _GH + "close " + _URL + " -d", _GH + "close 5 -d", False),
    ("url token -> cross-host url", _GH + "close " + _URL + " -d", _GH + "close " + _URL_ENT + " -d", False),
    ("url token -> different-repo url", _GH + "close " + _URL + " -d", _GH + "close " + _URL_OTHER + " -d", False),
    ("branch token -> bare number", _GH + "close feature -d", _GH + "close 5 -d", False),
    ("branch token -> different branch", _GH + "close feature -d", _GH + "close main -d", False),
    ("number token -> url", _GH + "close 5 -d", _GH + "close " + _URL + " -d", False),
    # commit-6 --repo mis-bind: the branch:feature token must NOT authorize a PR-5 close.
    # This is the CI-permanent known-bad for the mis-bind fix: if the identity coarsened
    # back to '5' (the mis-bind), this would AUTHORIZE instead of REFUSE.
    ("--repo branch token -> number", _GH + "close --repo 5/6 feature -d", _GH + "close 5 --repo 5/6 -d", False),
    ("faithful url == url", _GH + "close " + _URL + " -d", _GH + "close " + _URL + " -d", True),
    ("faithful branch == branch", _GH + "close feature -d", _GH + "close feature -d", True),
    ("faithful --repo branch == same", _GH + "close --repo 5/6 feature -d", _GH + "close --repo 5/6 feature -d", True),
]

# Case 1 (commit 6): the `--repo`/`-R` value mis-bind fix. `--repo` takes a repo value that
# can start with a digit (`5/6`); at commit-6's PARENT (7bb6ac5c) `_extract_pr_number`'s
# value-flag list OMITTED `--repo` while `_gh_close_positionals` INCLUDED it, so the repo
# value's leading digit shadowed the branch positional and mis-bound '5'. The fold aligns
# the lists (positional-first close derivation), so these FAITHFUL commands (real --repo flag
# + real branch positional, gh runs them) now mint the branch. Counter-test-by-revert
# (source -> 7bb6ac5c): these mint '5' (the mis-bind) — coupling them to commit 6.
CLOSE_REPO_MISBIND = [
    _GH + "close --repo 5/6 feature -d",
    _GH + "close -R 5/6 feature -d",
    _GH + "close --repo 55/66 feature -d",
]

# The value-taking gh pr flags `_extract_pr_number` must skip. The MERGE path still uses
# `_extract_pr_number` (byte-untouched by the close fold), so this pins the fold's structural
# claim that CLOSE AND MERGE DO NOT CROSS: merge minting is identical base(172a77dd) -> HEAD.
_GH_MERGE_VALUE_FLAGS = [
    "--body", "--body-file", "--subject", "--author-email", "--match-head-commit",
    "--comment", "--max-retries", "--retry-count", "--timeout",
]


# ═════════════════════════════════════════════════════════════════════════════════
# REAL post_main -> pre_main drivers (the D2 backbone; sibling of the obs-cert harness).
# _mint returns (count, token_context) — context read OFF the minted token on disk.
# ═════════════════════════════════════════════════════════════════════════════════
_ALLOW, _DENY = 0, 2

# The identity keys a token may bind (the which-key-won read).
_TARGET_KEYS = (
    "pr_number", "branch", "target_ref", "mass_target",
    "protected_branch", "branch_set", "push_set",
)


def _mint(cmd, tok):
    """Drive the REAL post hook with an approval embedding `cmd`. Returns
    (count_of_tokens_minted, context_dict_or_None) — the context is read from the token
    FILE, which is authoritative for which-key-won even when the whole-command extractor
    binds nothing (the compound-row artifact)."""
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
        "session_id": "cert-1134",
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
        "session_id": "cert-1134",
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
    """The single identity key bound in a token context (which-key-won)."""
    keys = [k for k in _TARGET_KEYS if ctx.get(k)]
    return keys[0] if len(keys) == 1 else keys


def _isolated_roundtrip(approve, execute):
    """Mint `approve`, then execute `execute`, in a token dir this call OWNS — allocated
    internally and torn down on return, so it is STRUCTURALLY IMPOSSIBLE to share across
    rows. Returns (minted, rc).

    TOKEN-DIR ISOLATION RULE (bit twice, now closed): any test that mints tokens in a LOOP
    must give each iteration a fresh token dir — a leaked token from an earlier row
    contaminates a later row's authorize/refuse outcome. It first hit a D2 gated-but-
    unmintable known-bad (shared tmp_path across a vacuity-mint and the neuter-exec), then
    RECURRED in the binding base-sim loop (one tmp_path across three escalation iterations),
    where the leak flipped --no-verify to a false "stays closed" and produced a wrong
    "doubly-defended" attribution. Parametrized single-row tests are safe (pytest gives each
    a fresh tmp_path); the hazard is a for-loop over rows inside ONE test. Route such loops
    through THIS helper so the dir cannot be shared by construction."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tok = Path(d)
        minted, _ = _mint(approve, tok)
        assert minted == 1, "the approval did not mint (round-trip is vacuous): %r" % approve
        return minted, _execute(execute, tok)


# ═════════════════════════════════════════════════════════════════════════════════
# CORPUS SHAPE — guards against silent corpus drift (a shrunk corpus is a wrong-mechanism
# model masquerading as a passing cert; three re-label counts 0/5/216 were all "correct"
# about their own corpus — the corpus is part of the claim).
# ═════════════════════════════════════════════════════════════════════════════════
class TestCorpusShape:
    def test_row_counts_are_pinned(self):
        assert len(MUST_FLIP) == 22, "MUST_FLIP drifted from 22"
        assert len(MUST_HOLD) == 29, "MUST_HOLD drifted from 29 (do NOT trim the union)"
        assert len(MUST_STAY_OFF) == 2, "MUST_STAY_OFF drifted from 2"
        assert len(BENIGN) == 8, "BENIGN drifted from 8"

    def test_count_pin_constant_matches_corpus(self):
        # The {22, 110} figure is derived from the corpus, not hard-typed independently,
        # so it cannot silently disagree with the rows it pins.
        assert COUNT_PIN_SPELLINGS == len(MUST_FLIP)
        assert COUNT_PIN_VIOLATIONS == len(MUST_FLIP) * len(PREFIXES) == 110


# ═════════════════════════════════════════════════════════════════════════════════
# D1 — is_dangerous_command, leg-position parity.
# ═════════════════════════════════════════════════════════════════════════════════
class TestD1MustFlip:
    """The under-block closure. bare is dangerous at base AND head; the NON-FIRST leg
    was False at base (the #1134 under-block) and is True at head across every prefix.
    The base fixture IS the demonstrated known-bad — the assertion cannot pass vacuously
    because the same rows measurably fail leg-2 at base."""

    @pytest.mark.parametrize(
        "cmd,op,key", MUST_FLIP, ids=[r[0] for r in MUST_FLIP]
    )
    def test_flip(self, cmd, op, key):
        # bare dangerous both sides (else it is not an under-block row at all).
        assert _base()(cmd) is True, "row not dangerous bare at base — mislabeled: %r" % cmd
        assert D(cmd) is True, "row lost bare coverage at head: %r" % cmd
        for label, p in PREFIXES:
            # KNOWN-BAD: leg-2 was UNGATED at base (the under-block the fix closes).
            assert _base()(p + cmd) is False, (
                "%s: leg-2 already gated at base — row does not demonstrate the #1134 "
                "under-block: %r" % (label, p + cmd)
            )
            # HEAD: leg-2 now gates (no under-reach).
            assert D(p + cmd) is True, (
                "#1134 UNDER-BLOCK RE-OPENED — non-first-leg destructive form ungated at "
                "head under %s: %r" % (label, p + cmd)
            )


class TestD1MustHold:
    """Working coverage that must not regress. Gated in BOTH positions at base and head.
    The base==True assertion is the VACUITY GUARD: a MUST_HOLD row that is False at base
    is mislabeled (it belongs in MUST_FLIP) and its 'stays gated' claim would be
    vacuous — so the row is required to stand on its own pre-fix coverage."""

    @pytest.mark.parametrize("cmd", MUST_HOLD, ids=MUST_HOLD)
    def test_hold(self, cmd):
        assert _base()(cmd) is True, "MUST_HOLD row not gated bare at base (mislabeled): %r" % cmd
        assert D(cmd) is True, "REGRESSION: MUST_HOLD bare coverage lost at head: %r" % cmd
        for label, p in PREFIXES:
            assert _base()(p + cmd) is True, (
                "MUST_HOLD row not gated in leg-2 at base under %s (mislabeled — belongs "
                "in MUST_FLIP): %r" % (label, p + cmd)
            )
            assert D(p + cmd) is True, (
                "REGRESSION: MUST_HOLD working leg-2 coverage lost under %s: %r"
                % (label, p + cmd)
            )


class TestD1MustStayOff:
    """The over-block guard for the close/option parser. NOT dangerous even bare, and
    still not dangerous in any leg position — at base AND head. A change that gates these
    has introduced the cardinal over-block."""

    @pytest.mark.parametrize("cmd", MUST_STAY_OFF, ids=MUST_STAY_OFF)
    def test_stay_off(self, cmd):
        assert _base()(cmd) is False, "MUST_STAY_OFF row already gated at base (mislabeled): %r" % cmd
        assert D(cmd) is False, "OVER-BLOCK introduced (cardinal) — bare form gated: %r" % cmd
        for label, p in PREFIXES:
            assert D(p + cmd) is False, (
                "OVER-BLOCK introduced (cardinal) — benign form gated in leg-2 under %s: %r"
                % (label, p + cmd)
            )


class TestD1BenignParitySweep:
    """THE LOAD-BEARING GATE (over-block direction): is_dangerous(P + C) == is_dangerous(C)
    for every benign prefix P and benign command C, and all-False. Over-reach shows as a
    benign parity violation; this is the live guard for the STATED RESIDUAL (an unfound
    first-leg false positive whose reach the widening would multiply across legs). It
    bounds the CLASS of over-blocks we enumerated — NOT the COUNT of first-leg false
    positives never enumerated. Complementary to TestD1MustFlip's parity (under-reach) —
    neither supersedes the other."""

    @pytest.mark.parametrize("cmd", BENIGN, ids=BENIGN)
    def test_benign_parity_and_all_false(self, cmd):
        assert D(cmd) is False, "benign command gated bare (first-leg false positive): %r" % cmd
        for label, p in PREFIXES:
            assert D(p + cmd) == D(cmd), (
                "LEG-POSITION PARITY VIOLATION (over-reach) — benign verdict changed by a "
                "benign %s prefix: %r" % (label, p + cmd)
            )
            assert D(p + cmd) is False, (
                "benign command gated in leg-2 under %s (over-block): %r" % (label, p + cmd)
            )


class TestBenignWordBoundaryKnownBad:
    """Non-vacuity for the benign branch-creation rows: the `-D\\b` word boundary on the
    branch-delete literal arm is the invariant this arc found with NO check at all —
    removing it gates ordinary branch CREATIONS (`git branch feature-Dashboard`) with a
    suite failure set otherwise identical to control. Here the benign guard is shown to
    FAIL on that exact known-bad, then to hold once restored."""

    def test_boundary_removal_overblocks_then_restore(self, monkeypatch):
        import re
        arms = mgc._BRANCH_DELETE_LITERAL_ARMS
        # VACUITY GUARD: the -D arm must actually carry `-D\b`, else the neuter is a no-op.
        d_arm = next((a for a in arms if r"-D\b" in a.pattern), None)
        assert d_arm is not None, (
            "no branch-delete literal arm carries `-D\\b` — the boundary neuter below "
            "would prove nothing; the arm shape changed"
        )
        # Baseline: benign branch-creations stay off.
        for cmd in BENIGN_BRANCH_CREATION:
            assert D(cmd) is False, "benign branch-creation already gated pre-neuter: %r" % cmd
        # KNOWN-BAD: strip the boundary off the -D arm.
        neutered = tuple(
            re.compile(a.pattern.replace(r"-D\b", "-D")) if a is d_arm else a
            for a in arms
        )
        monkeypatch.setattr(mgc, "_BRANCH_DELETE_LITERAL_ARMS", neutered)
        overblocked = [cmd for cmd in BENIGN_BRANCH_CREATION if D(cmd) is True]
        assert overblocked, (
            "removing `-D\\b` did NOT over-block any benign branch-creation — the guard "
            "the benign rows provide is not attributable to the word boundary; "
            "re-examine what actually keeps these ungated"
        )


class TestCountPin:
    """{22 spellings, 110 violations} -> {0, 0}. The parity-violation cardinality over the
    MUST_FLIP spellings: at base every spelling violates leg-position parity in all 5
    prefixes (the under-block); at head none does. The base figure is the known-bad."""

    def _violations(self, danger):
        spellings, n = set(), 0
        for cmd, _op, _key in MUST_FLIP:
            for _label, p in PREFIXES:
                if danger(p + cmd) != danger(cmd):
                    spellings.add(cmd)
                    n += 1
        return len(spellings), n

    def test_head_is_zero(self):
        assert self._violations(D) == (0, 0), (
            "leg-position parity violations remain at head — the #1134 under-block is "
            "not fully closed"
        )

    def test_base_is_the_known_bad(self):
        assert self._violations(_base()) == (COUNT_PIN_SPELLINGS, COUNT_PIN_VIOLATIONS), (
            "base did not exhibit {22, 110} violations — the vendored fixture is not the "
            "pre-#1134 state, so the head==0 pin certifies nothing"
        )


class TestD1PerSiteFilterNeuter:
    """MECHANISM ISOLATION (is_dangerous): the per-leg _PER_LEG_OPS filter is the SOLE
    thing gating every MUST_FLIP row in a non-first leg (no literal arm reaches them
    there — that is why they were left behind). Reverting the filter to its pre-fix
    2-tuple drops all 22. `_PER_LEG_OPS` is referenced as a CALL-TIME module attribute,
    so a revert of the rename (AttributeError) or the widening (this test) fails loudly."""

    def test_filter_is_load_bearing_for_gating(self, monkeypatch):
        # VACUITY GUARD: the 4 new classes must currently be in the filter, else the
        # neuter is a no-op and this proves nothing.
        assert set(NEW_PER_LEG_CLASSES) <= set(mgc._PER_LEG_OPS), (
            "the 4 #1134 classes are not in _PER_LEG_OPS — the neuter below is a no-op"
        )
        monkeypatch.setattr(mgc, "_PER_LEG_OPS", ("push-to-main", "force-push"))
        survivors = [
            cmd for cmd, _op, _key in MUST_FLIP if D(_PREFIX + cmd) is True
        ]
        assert survivors == [], (
            "with the filter reverted to its pre-fix 2-tuple, these leg-2 rows STILL "
            "gate — a second mechanism covers them, so the filter is not the attributable "
            "cause and TestD1MustFlip cannot credit it: %r" % survivors
        )


# ═════════════════════════════════════════════════════════════════════════════════
# D2 — mintability. A gated form whose target cannot be bound is the cardinal
# gated-but-unmintable over-block. minted==1 asserted FIRST; which-key-won read OFF the
# token; exec-without-token DENY is the fix-coupled leg (base = ungated -> ALLOW).
# ═════════════════════════════════════════════════════════════════════════════════
class TestD2Mintability:
    """For every newly-gated MUST_FLIP row: gated at head, DENIED without a token (proves
    the gate — the fix; base known-bad = ungated), MINTS exactly one token that binds the
    RIGHT op and the RIGHT identity key (read off the token, not the whole-command
    context), and self-authorizes ALLOW. The exec-without-token DENY is what makes the
    round-trip fix-coupled rather than pipeline-blind."""

    @pytest.mark.parametrize(
        "cmd,op,key", MUST_FLIP, ids=[r[0] for r in MUST_FLIP]
    )
    def test_gated_and_mintable(self, cmd, op, key, tmp_path):
        row = _PREFIX + cmd  # certify the NON-FIRST-leg form (the fix's population)
        assert D(row) is True, "row not gated at head (D1 precondition): %r" % row
        # KNOWN-BAD leg: at base this ran ungated; at head, no token -> DENY (gated).
        assert _base()(row) is False, "row already gated at base — not a #1134 flip: %r" % row
        assert _execute(row, tmp_path) == _DENY, (
            "gated row was ALLOWED with NO token — the gate is not live at exec time: %r" % row
        )
        # MINT (minted==1 FIRST — a mint-side miss must not masquerade as a read decision).
        minted, ctx = _mint(row, tmp_path)
        assert minted == 1, (
            "gated row did NOT mint — the cardinal gated-but-unmintable OVER-BLOCK: %r" % row
        )
        # which-key-won, OFF THE TOKEN (whole-command extract binds nothing on compounds).
        assert ctx.get("operation_type") == op, (
            "token bound the wrong op: got %r, expected %r for %r"
            % (ctx.get("operation_type"), op, row)
        )
        assert _won_key(ctx) == key, (
            "token bound the wrong identity key: got %r, expected %r for %r"
            % (_won_key(ctx), key, row)
        )
        # self-authorize: the faithful click's own token ALLOWs its own execution.
        assert _execute(row, tmp_path) == _ALLOW, (
            "the faithful click's own token did NOT authorize its execution: %r" % row
        )


class TestD2GatedButUnmintableKnownBad:
    """Non-vacuity for minted==1: a mint-target neuter (the exact #1129 shape — a key
    dropped from _target_value) turns a gated row gated-but-UNMINTABLE, so the faithful
    click is refused with no way to authorize. Demonstrates the minted==1 assertions fail
    on a known-bad. `_target_value` referenced call-time via the module."""

    def test_dropping_target_ref_makes_a_gated_row_unmintable(self, tmp_path, monkeypatch):
        row = _PREFIX + _PG + "origin --delete feature"  # remote-ref-delete -> target_ref
        # VACUITY GUARD in its OWN dir (its token must not leak into the known-bad exec).
        vac = tmp_path / "vac"
        vac.mkdir()
        minted, ctx = _mint(row, vac)
        assert minted == 1 and _won_key(ctx) == "target_ref", (
            "row does not mint on target_ref at head — pick a different witness"
        )

        def _tv_without_target_ref(cmd_ctx):
            return (cmd_ctx.get("pr_number")
                    or cmd_ctx.get("branch")
                    or cmd_ctx.get("branch_set")
                    or cmd_ctx.get("mass_target")
                    or cmd_ctx.get("protected_branch"))  # target_ref DROPPED

        monkeypatch.setattr(mgpost, "_target_value", _tv_without_target_ref)
        # KNOWN-BAD in a FRESH dir: mint 0 -> the exec finds no token -> DENY. The faithful
        # click is refused with no way to authorize = the gated-but-unmintable over-block.
        kb = tmp_path / "kb"
        kb.mkdir()
        m2, _ = _mint(row, kb)
        assert m2 == 0, "mint-target neuter did not stop the mint — the guard is inert"
        assert _execute(row, kb) == _DENY, (
            "gated-but-unmintable row was not denied — the round-trip cannot see the "
            "over-block it exists to catch"
        )


class TestD2MintPinCloseAndBranchDelete:
    """GAP-CLOSURE (security review): mint-pin `close` and `branch-delete` in a non-first
    leg — the two classes the shipped mint-path probe's 4-tuple EXCLUDED, leaving them
    gate-pinned only. Each row: gated at head, mints exactly one token binding the RIGHT
    op and RIGHT identity key (read OFF the token), and self-authorizes. Two carriers so
    the pin is not a single-prefix artifact."""

    @pytest.mark.parametrize(
        "cmd,op,key", MINT_PIN_CLOSE_AND_BRANCH_DELETE,
        ids=[r[0] for r in MINT_PIN_CLOSE_AND_BRANCH_DELETE],
    )
    def test_close_and_branch_delete_mint_in_non_first_leg(self, cmd, op, key, tmp_path):
        assert D(cmd) is True, "row not gated at head (mint-pin precondition): %r" % cmd
        minted, ctx = _mint(cmd, tmp_path)
        assert minted == 1, (
            "GATE-PINNED BUT NOT MINT-PINNED — %s in a non-first leg gates but does not "
            "mint (the exclusion the 4-tuple probe hid): %r" % (op, cmd)
        )
        assert ctx.get("operation_type") == op, (
            "token bound wrong op: got %r expected %r for %r"
            % (ctx.get("operation_type"), op, cmd)
        )
        assert _won_key(ctx) == key, (
            "token bound wrong identity key: got %r expected %r for %r"
            % (_won_key(ctx), key, cmd)
        )
        assert _execute(cmd, tmp_path) == _ALLOW, (
            "the faithful click's own token did not authorize its execution: %r" % cmd
        )


class TestCloseUrlBranchNowGateAndMint:
    """THE POSITIONAL-TYPE AXIS THE CORPUS MISSED. `gh pr close` accepts number|url|branch;
    the corpus covered only number, so a faithful gated-but-unmintable OVER-BLOCK hid in the
    url/branch forms (NFL commit-1-introduced; FL pre-existing). The commit-5 extractor fix
    makes them gate+MINT with a positional-type-qualified `pr_number` value. Per row: the
    base differential is measured via the 172a77dd fixture (NFL base-ungated = D1 gate-flip;
    FL base-gated = D2 — its base mint0 is the over-block, documented in the HANDOFF via a
    base worktree); at the fix the form gates, mints ONE token binding op=close and the RIGHT
    qualified value (read OFF the token, since whole-command _target_value returns None on
    the compound), and self-authorizes."""

    @pytest.mark.parametrize(
        "cmd,value,base_gated", CLOSE_URL_BRANCH_FLIP,
        ids=[r[0] for r in CLOSE_URL_BRANCH_FLIP],
    )
    def test_url_branch_close_gate_and_mint(self, cmd, value, base_gated, tmp_path):
        # Base differential (is_dangerous, via the fixture): D1 rows base-ungated, D2 rows
        # base-gated. This is the known-bad axis: at base these forms either did not gate
        # (D1) or gated-but-could-not-mint (D2) — either way the faithful click was blocked.
        assert _base()(cmd) is base_gated, (
            "base is_dangerous kind changed for %r (expected base_gated=%s)" % (cmd, base_gated)
        )
        # Fixed: gates, mints the qualified identity, self-authorizes.
        assert D(cmd) is True, "the fix did not gate the faithful close form: %r" % cmd
        minted, ctx = _mint(cmd, tmp_path)
        assert minted == 1, (
            "OVER-BLOCK persists — the faithful close form gates but does NOT mint "
            "(gated-but-unmintable): %r" % cmd
        )
        assert ctx.get("operation_type") == "close", (
            "token bound the wrong op: %r for %r" % (ctx.get("operation_type"), cmd)
        )
        assert ctx.get("pr_number") == value, (
            "wrong close identity: got %r, expected the positional-type-qualified %r for %r"
            % (ctx.get("pr_number"), value, cmd)
        )
        assert _execute(cmd, tmp_path) == _ALLOW, (
            "the faithful close click's own token did not self-authorize: %r" % cmd
        )


class TestCloseMintLaundering:
    """The mint-laundering guard for the positional-type-qualified close identity: a token
    minted for one spelling (url / branch / number) must NOT authorize a DIFFERENT-identity
    execution — including the cross-host and cross-repo cases the host-qualified value exists
    to distinguish. Faithful self-executions must still AUTHORIZE (no over-block). Every
    escalation row is its own known-bad: the identity qualification is what makes it refuse,
    and a coarsening that dropped the host/repo/branch qualifier would flip it to AUTHORIZE."""

    @pytest.mark.parametrize(
        "name,approve,execute,expect_allow", CLOSE_LAUNDERING,
        ids=[r[0] for r in CLOSE_LAUNDERING],
    )
    def test_close_identity_laundering(self, name, approve, execute, expect_allow, tmp_path):
        minted, _ = _mint(approve, tmp_path)
        assert minted == 1, "approve did not mint (%s): %r" % (name, approve)
        rc = _execute(execute, tmp_path)
        if expect_allow:
            assert rc == _ALLOW, (
                "OVER-BLOCK: a faithful close self-execution was REFUSED (%s): %r"
                % (name, execute)
            )
        else:
            assert rc == _DENY, (
                "LAUNDERING: a close token authorized a DIFFERENT-identity execution "
                "(%s): approve=%r execute=%r" % (name, approve, execute)
            )


class TestCloseRepoValueMisbindClosed:
    """Commit 6: the `--repo`/`-R` value mis-bind. A repo value starting with a digit
    (`--repo 5/6`) shadowed the branch positional and mis-bound '5' at commit-6's parent
    (7bb6ac5c), because `_extract_pr_number`'s value-flag list omitted `--repo` while
    `_gh_close_positionals` included it. The positional-first fold aligns them. These are
    FAITHFUL commands (real flag + real positional). The CI-permanent known-bad is the
    laundering row (`--repo branch token -> number` in CLOSE_LAUNDERING): if the identity
    coarsened back to '5', that row would AUTHORIZE. Counter-test-by-revert to 7bb6ac5c
    (mints '5') is documented in the HANDOFF."""

    @pytest.mark.parametrize("cmd", CLOSE_REPO_MISBIND, ids=CLOSE_REPO_MISBIND)
    def test_repo_value_does_not_shadow_branch_positional(self, cmd, tmp_path):
        assert D(cmd) is True, "faithful --repo close form not gated: %r" % cmd
        minted, ctx = _mint(cmd, tmp_path)
        assert minted == 1, "faithful --repo close form did not mint: %r" % cmd
        assert ctx.get("operation_type") == "close"
        assert ctx.get("pr_number") == "branch:feature", (
            "MIS-BIND: --repo value shadowed the branch positional — bound %r, expected "
            "'branch:feature' (the repo value's leading digit was mistaken for the PR): %r"
            % (ctx.get("pr_number"), cmd)
        )
        assert _execute(cmd, tmp_path) == _ALLOW, "faithful --repo close did not self-authorize: %r" % cmd


class TestMergeExtractorUnchangedByCloseFold:
    """The fold restructured CLOSE to derive from `_gh_close_positionals` and NOT call
    `_extract_pr_number`; the MERGE path STILL uses `_extract_pr_number`. This pins the
    structural claim that CLOSE AND MERGE DO NOT CROSS — merge value-flag minting is
    IDENTICAL base(172a77dd) -> HEAD across the full 9-flag value-taking set. Known-bad: if
    the fold had touched the shared extractor or its flag set, a merge command with a numeric
    value-flag value would mint the flag's value instead of the positional PR."""

    def test_value_flag_set_byte_identical_base_to_head(self):
        base_set = getattr(load_baseline_172a77dd(), "_GH_PR_VALUE_TAKING_FLAGS", None)
        assert base_set == mgc._GH_PR_VALUE_TAKING_FLAGS, (
            "the gh pr value-taking flag set changed base->HEAD — the close fold touched the "
            "merge-shared extractor's vocabulary: base=%r head=%r"
            % (base_set, mgc._GH_PR_VALUE_TAKING_FLAGS)
        )
        assert len(mgc._GH_PR_VALUE_TAKING_FLAGS) == 9, (
            "expected 9 value-taking flags; the set changed size: %r"
            % (sorted(mgc._GH_PR_VALUE_TAKING_FLAGS),)
        )

    @pytest.mark.parametrize("flag", _GH_MERGE_VALUE_FLAGS, ids=_GH_MERGE_VALUE_FLAGS)
    def test_merge_extractor_binds_positional_not_flag_value(self, flag):
        # A merge command with a numeric value-flag value must bind the POSITIONAL PR (42),
        # not the flag's value (99) — and identically at base and HEAD (the pure extractor,
        # no post hook needed).
        cmd = _GH + "merge 42 " + flag + " 99 --admin"
        base = load_baseline_172a77dd()._extract_pr_number(cmd)
        head = mgc._extract_pr_number(cmd)
        assert base == head == "42", (
            "merge extractor changed or mis-binds under %s: base=%r head=%r (expected '42' "
            "both — the flag value 99 must not shadow the positional): %r"
            % (flag, base, head, cmd)
        )

    def test_merge_mints_positional_end_to_end(self, tmp_path):
        # Behavioral backstop through the REAL post hook: merge still mints the positional.
        cmd = _GH + "merge 42 --comment 99 --admin"
        minted, ctx = _mint(cmd, tmp_path)
        assert minted == 1 and ctx.get("pr_number") == "42", (
            "merge no longer mints the positional PR through the real hook: minted=%s pr=%r"
            % (minted, ctx.get("pr_number") if ctx else None)
        )


class TestD2RoundTripIsPipelineBlindWithoutTheGate:
    """DOCUMENTED TRAP — do NOT 'simplify' D2 to mint + self-authorize ALLOW. Under an
    _PER_LEG_OPS revert the row goes UNGATED yet mint+exec still returns (1, ALLOW): the
    mint uses the fix-independent _extraction_surface and the exec ALLOWs because the row
    is no longer gated. This asserts that blindness EXPLICITLY, so the exec-without-token
    DENY in TestD2Mintability is understood as the fix-coupled leg, not a redundant one."""

    def test_mint_plus_allow_survives_the_fix_revert(self, tmp_path, monkeypatch):
        row = _PREFIX + _PG + "origin --delete feature"
        monkeypatch.setattr(mgc, "_PER_LEG_OPS", ("push-to-main", "force-push"))
        assert D(row) is False, "expected the revert to ungate the row (vacuity guard)"
        minted, _ = _mint(row, tmp_path)
        assert minted == 1, "mint is fix-independent (uses _extraction_surface) — expected 1"
        assert _execute(row, tmp_path) == _ALLOW, (
            "expected ALLOW-because-ungated under the revert — this is the blindness the "
            "exec-without-token DENY exists to close"
        )


# ═════════════════════════════════════════════════════════════════════════════════
# D3 — detect_command_operation_type, op-parity (mint==read: detect non-None <=> gated).
# ═════════════════════════════════════════════════════════════════════════════════
class TestD3OpParity:
    """The mint==read symmetry, in the direction that is load-bearing: GATED ⟹ CLASSIFIED.
    Every gated form MUST have a non-None op so the mint can bind a target — a gated form
    detect cannot classify is the gated-but-unmintable over-block. The REVERSE (classified
    ⟹ gated) is deliberately NOT asserted: `gh pr close 5` is op-classified `close` yet
    correctly UNGATED (a reversible close is not dangerous), so full equivalence is false
    by design. The over-broad-gating direction is guarded by TestD1MustStayOff /
    TestD1BenignParitySweep (is_dangerous=False), not here."""

    _GATED = (
        [(c, op) for c, op, _k in MUST_FLIP]
        + [(c, None) for c in MUST_HOLD]     # op unspecified: only gated⟹classified pinned
    )

    @pytest.mark.parametrize("cmd,op", _GATED, ids=[r[0] for r in _GATED])
    def test_gated_implies_classified(self, cmd, op):
        for _label, p in ([("bare", "")] + PREFIXES):
            full = p + cmd
            if D(full) is True:
                assert DETECT(full) is not None, (
                    "GATED-BUT-UNMINTABLE: gated form has op=None, the mint cannot bind a "
                    "target -> the faithful click is refused with no way to authorize: %r"
                    % full
                )
        if op is not None:
            # the fix must classify the newly-gated form with the RIGHT op, in leg-2.
            assert DETECT(_PREFIX + cmd) == op, (
                "detect op-parity: got %r, expected %r for %r"
                % (DETECT(_PREFIX + cmd), op, _PREFIX + cmd)
            )

    @pytest.mark.parametrize("cmd", BENIGN, ids=BENIGN)
    def test_benign_is_unclassified(self, cmd):
        # A benign command the op-classifier recognizes is a smell — the next widening
        # that gates its op would over-block it. (MUST_STAY_OFF is exempt: a reversible
        # close IS a recognized op, correctly ungated.)
        for _label, p in ([("bare", "")] + PREFIXES):
            assert DETECT(p + cmd) is None, (
                "benign command classified as %r under %s — a widening of that op would "
                "over-block it: %r" % (DETECT(p + cmd), _label, p + cmd)
            )


class TestD3PerSiteFilterNeuter:
    """MECHANISM ISOLATION (detect): the per-leg filter drives detect for the 18 non-close
    rows (None at base, classified at head). The 4 CLOSE rows are deliberately EXCLUDED —
    they carry detect==close at base via the raw-fallback gh-pr arm, NOT the filter, so a
    'filter reverted -> detect None' claim is FALSE for them (they keep detect==close).
    Asserting on them would be the two-mechanism endpoint blindness. Reverting the filter
    drops detect on the 18; the 4 close rows are pinned to KEEP detect==close as the
    control that the exclusion is real."""

    def test_filter_drives_detect_on_non_close_rows(self, monkeypatch):
        assert set(NEW_PER_LEG_CLASSES) <= set(mgc._PER_LEG_OPS), "vacuity guard: filter no-op"
        monkeypatch.setattr(mgc, "_PER_LEG_OPS", ("push-to-main", "force-push"))
        survivors = [
            cmd for cmd, _op, _key in MUST_FLIP_FILTER_DRIVES_DETECT
            if DETECT(_PREFIX + cmd) is not None
        ]
        assert survivors == [], (
            "with the filter reverted, these non-close leg-2 rows STILL classify — a "
            "second detect mechanism covers them, so the filter is not attributable: %r"
            % survivors
        )

    def test_close_rows_keep_detect_via_raw_fallback(self, monkeypatch):
        # The isolation control: close detect is NOT the filter's, so it survives the
        # revert. (If this ever fails, the close family's detect mechanism moved and the
        # exclusion above must be re-derived.)
        monkeypatch.setattr(mgc, "_PER_LEG_OPS", ("push-to-main", "force-push"))
        for cmd, op, _key in MUST_FLIP:
            if op == "close":
                assert DETECT(_PREFIX + cmd) == "close", (
                    "close leg-2 detect vanished under the filter revert — it was NOT "
                    "carried by the raw-fallback arm after all: %r" % (_PREFIX + cmd)
                )


class TestConsumerSharedObjectInvariant:
    """The mint==read symmetry is preserved BY CONSTRUCTION because both consumers
    (detect_command_operation_type and _stripped_surface_danger) read the SAME shared
    _PER_LEG_OPS object at call time. This guards the refactor that duplicates the
    constant into two literals: mutating the module global must move BOTH surfaces
    identically. If one consumer held a private copy, its verdict would not change and
    the symmetry TestD3OpParity checks could later be weakened without notice."""

    def test_both_consumers_track_the_same_module_global(self, monkeypatch):
        probe = _PREFIX + _PG + "origin main"  # push-to-main leg-2, filter-carried
        assert D(probe) is True and DETECT(probe) == "push-to-main", "baseline moved"
        # Emptying the ONE global must drop BOTH the read floor (via _stripped_surface_
        # danger, reached through is_dangerous) AND detect. A private copy in either
        # consumer would keep that consumer's verdict unchanged — the known-bad.
        monkeypatch.setattr(mgc, "_PER_LEG_OPS", ())
        assert D(probe) is False, (
            "is_dangerous still gated after emptying _PER_LEG_OPS — _stripped_surface_"
            "danger is reading a private copy, not the shared global"
        )
        assert DETECT(probe) is None, (
            "detect still classified after emptying _PER_LEG_OPS — detect is reading a "
            "private copy, not the shared global"
        )


# ═════════════════════════════════════════════════════════════════════════════════
# BINDING — the remote-delete PRIVILEGED_FLAGS split, certified BEHAVIORALLY end-to-end.
# The most severe finding of the arc: a single-branch-delete approval must NOT authorize
# a wholesale --mirror remote wipe. The structural data-shape of the split is pinned in
# test_merge_guard_privileged_flags.py::TestRemoteDeleteSplitBinding; here it is the
# escalation OUTCOME, with its known-bad.
# ═════════════════════════════════════════════════════════════════════════════════
# The security-engineer's 7 split-binding rows, re-measured HERE via the REAL round-trip
# (stronger than the probe's replicated identity comparison). 3 escalation-closure rows
# (approve a single/mass delete; an execution ADDING a scope/privilege flag REFUSES) + 4
# faithful-preserved rows (approve==execute AUTHORIZES, no over-block). Flags assembled at
# runtime so the file stays inert. (name, approve_cmd, execute_cmd, expect_allow.)
_DEL, _MIR, _PRU, _NOV = "--delete", "--mirror", "--prune", "--no-verify"
SPLIT_BINDING_ROWS = [
    ("esc :main -> --mirror :main", _PG + "origin :main", _PG + "origin " + _MIR + " :main", False),
    ("esc :main -> --prune :main", _PG + "origin :main", _PG + "origin " + _PRU + " :main", False),
    ("esc del -> del --no-verify", _PG + "origin " + _DEL + " feat",
     _PG + "origin " + _DEL + " feat " + _NOV, False),
    ("faithful --mirror", _PG + _MIR + " origin", _PG + _MIR + " origin", True),
    ("faithful :main", _PG + "origin :main", _PG + "origin :main", True),
    ("faithful del", _PG + "origin " + _DEL + " feat", _PG + "origin " + _DEL + " feat", True),
    ("faithful del+no-verify", _PG + "origin " + _DEL + " feat " + _NOV,
     _PG + "origin " + _DEL + " feat " + _NOV, True),
]
SPLIT_BINDING_ESCALATIONS = [r for r in SPLIT_BINDING_ROWS if r[3] is False]


class TestBindingEscalationClosed:
    """The remote-delete split PRIVILEGED_FLAGS binding, certified BEHAVIORALLY end-to-end.
    The most severe finding of the arc: approving a single/mass delete must NOT authorize
    an execution that ADDS a scope-widening (--mirror/--prune) or privilege (--no-verify)
    flag. minted==1 asserted FIRST on every escalation so a DENY is a READ decision, never
    a mint-side miss. The base-simulation and per-flag unbind are the demonstrated
    known-bads: without the binding the escalations AUTHORIZE."""

    def _roundtrip(self, approve, execute, tok):
        # tok MUST be a fresh dir per call — a shared dir leaks tokens across rows and
        # silently changes outcomes (a token-leak bug that once produced a false
        # "--no-verify stays closed" reading in this very class).
        minted, _ = _mint(approve, tok)
        assert minted == 1, "the delete approval did not mint: %r" % approve
        return _execute(execute, tok)

    def test_single_branch_approval_denies_mirror_wipe(self, tmp_path):
        rc = self._roundtrip(_PG + "origin :main", _PG + "--mirror origin :main", tmp_path)
        assert rc == _DENY, (
            "SEVERE: a single-branch-delete approval AUTHORIZED a wholesale --mirror "
            "remote wipe — the identity-coarsening escalation is open"
        )

    @pytest.mark.parametrize(
        "name,approve,execute,expect_allow", SPLIT_BINDING_ROWS,
        ids=[r[0] for r in SPLIT_BINDING_ROWS],
    )
    def test_split_binding_rows(self, name, approve, execute, expect_allow, tmp_path):
        rc = self._roundtrip(approve, execute, tmp_path)
        if expect_allow:
            assert rc == _ALLOW, (
                "OVER-BLOCK: faithful approve==execute was REFUSED (%s): %r" % (name, execute)
            )
        else:
            assert rc == _DENY, (
                "ESCALATION OPEN: an execution adding a scope/privilege flag was "
                "AUTHORIZED by a plainer approval (%s): approve=%r execute=%r"
                % (name, approve, execute)
            )

    def test_stripping_binding_reopens_all_three_escalations(self, monkeypatch):
        # KNOWN-BAD (base simulation): strip the commit-2 bindings from the remote-delete
        # ops. ALL THREE escalations (--mirror, --prune, --no-verify) must REOPEN
        # (AUTHORIZE) — the binding is the SOLE closer for every one, so every escalation
        # row is non-vacuous. This STRENGTHENS commit 2 (the binding closes all three, not
        # just the two scope-widening flags).
        #
        # Corroborated two ways beyond this in-process monkeypatch: (a) a TRUE-BASE
        # detached worktree at 172a77dd — where remote-ref-delete's binding is natively
        # absent — AUTHORIZES all three via the real round-trip; (b) the security review's
        # independent measurement. --no-verify is pre-bound on the FORCE-PUSH op-class,
        # but PRIVILEGED_FLAGS is indexed by op_type, so that entry is never consulted for
        # a remote-ref-delete command — there is NO cross-op-class defense-in-depth here.
        #
        # METHOD NOTE (a bug this test previously had): each row MUST use a FRESH token dir.
        # An earlier version looped all three through one shared dir; leaked tokens made
        # --no-verify falsely read as "stays closed", which is where a wrong
        # "doubly-defended" attribution came from. Per-row fresh dirs is the fix.
        assert "--no-verify" in mgc.PRIVILEGED_FLAGS["remote-ref-delete"], (
            "remote-ref-delete does not bind --no-verify — the strip below is a no-op"
        )
        monkeypatch.setitem(mgc.PRIVILEGED_FLAGS, "remote-ref-delete", {})
        monkeypatch.setitem(mgc.PRIVILEGED_FLAGS, "remote-mass-delete", {})
        # _isolated_roundtrip owns a fresh token dir per call — the loop CANNOT share one
        # (the token-leak that produced the wrong "doubly-defended" reading is structurally
        # closed here).
        still_closed = [
            name for name, approve, execute, _ in SPLIT_BINDING_ESCALATIONS
            if _isolated_roundtrip(approve, execute)[1] != _ALLOW
        ]
        assert still_closed == [], (
            "with the remote-delete bindings stripped, these escalations did NOT reopen: "
            "%r — the binding is not their attributable closer, so their cert rows would "
            "prove nothing (or the strip is not reaching the read gate)." % (still_closed,)
        )

    def test_unbinding_mirror_reopens_the_escalation(self, tmp_path, monkeypatch):
        # Per-flag known-bad: --mirror specifically must be bound; removing just it must
        # reopen the mirror wipe (ALLOW), proving that binding is the attributable cause.
        assert "--mirror" in mgc.PRIVILEGED_FLAGS["remote-ref-delete"], (
            "--mirror is not bound on remote-ref-delete — the closure is not attributable "
            "to this binding; the escalation rows prove nothing"
        )
        stripped = {
            k: v for k, v in mgc.PRIVILEGED_FLAGS["remote-ref-delete"].items()
            if k != "--mirror"
        }
        monkeypatch.setitem(mgc.PRIVILEGED_FLAGS, "remote-ref-delete", stripped)
        rc = self._roundtrip(_PG + "origin :main", _PG + "--mirror origin :main", tmp_path)
        assert rc == _ALLOW, (
            "unbinding --mirror did NOT reopen the wipe escalation — the binding is inert, "
            "so its cert row proves nothing"
        )
