"""Completeness + adversarial regression suite for the merge-guard op-recognition
closure — a SACROSANCT honest-mistake auth control.

THREAT MODEL (honest-mistake, NOT adversarial defense): a faithful single-command
click ALWAYS mints + executes; UNDER-BLOCK of an honest destructive form is NEVER
acceptable; over-block of a faithful form is fails-safe. Adversarial obfuscation
(quote-elision, interpreter-pipe, $-expansion, var-laundering) is EXPLICITLY OUT
OF SCOPE.

This suite codifies the architect's TEST surface (the A-I spec) for THREE new
op-classes plus the HTTP-client data-body strip ("carrier-8"), and goes BEYOND the
literal enumeration: the literal-floor backstop was removed (it caused over-blocks),
so the enumeration ITSELF is now the proof that the whole faithful-spelling space
gates. An un-enumerated faithful spelling that silently fails to gate is an auth
bypass nothing else catches — so the no-under-block arms HUNT the faithful space
(refs/heads/ + refs/tags/ colon forms, lease `+:` forms, alternate flag orderings,
deep-slashed refs, implicit/explicit-remote cross product, multi-flag mass combos).

NON-VACUITY is mandatory on a SACROSANCT control: a green suite that cannot fail is
worthless. Every recognition arm asserts the EXACT bound target (not just
is_dangerous), every negative is paired with a discriminating positive, and the
highest-consequence canaries (mint==read parity, the end-to-end mint authorize
proof) carry an explicit counter-mutation demonstrating the canary CAN go red.

The three op-classes are enumerated INDEPENDENTLY (per the enlarged blind/Copilot
review surface): remote-ref-delete, remote-mass-delete, branch-protection each get
their own no-under-block AND no-over-block classes.

Empirical, not mocked: every test imports + calls the real production functions
(is_dangerous_command / detect_command_operation_type / extract_command_context and
the real mint path _mint_context_from_bundle / _target_value / _token_matches_command).
"""

import contextlib
import re as _real_re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import shared.merge_guard_common as mgc  # noqa: E402  (module handle for counter-mutation)
from shared.merge_guard_common import (  # noqa: E402
    is_dangerous_command as D,
    detect_command_operation_type as OP,
    extract_command_context as CTX,
)
from merge_guard_pre import _token_matches_command as MATCH  # noqa: E402
from merge_guard_post import (  # noqa: E402
    _mint_context_from_bundle,
    _target_value,
)

IMPLICIT = "\x00implicit"  # the implicit-remote marker used by remote-mass-delete


# ---------------------------------------------------------------------------
# Helpers — the real mint path and a token builder.
# ---------------------------------------------------------------------------

def mint(cmd_text):
    """Drive the REAL mint path: place a faithful command in an affirmative
    AskUserQuestion option and mint a token bundle. Returns MintResult(context,
    refusal_reason). curl/wget commands must be backtick-wrapped in the option
    text to mint (pre-existing locate_command_regions substrate behavior — the
    bare-curl/wget non-mint is a fail-safe over-block characteristic, accepted)."""
    question = {
        "question": "Proceed?",
        "options": [{"label": "Yes, do it", "description": f"Run `{cmd_text}` now"}],
        "multiSelect": False,
    }
    return _mint_context_from_bundle([question], {"Proceed?": "Yes, do it"})


def token_from_ctx(ctx):
    """Build the pre.py token shape from a minted/extracted context dict."""
    return {"operation_type": ctx["operation_type"], "context": ctx}


def token(op, **ctx_fields):
    """Construct a token directly for cross-auth / non-authorization probes."""
    ctx = {"operation_type": op, "bound_flags": [], **ctx_fields}
    return {"operation_type": op, "context": ctx}


class _ContentsGuardDisabledRe:
    """A drop-in for merge_guard_common's `re` module that forces
    re.search(r'contents/', ...) to return None — surgically disabling ONLY the
    carrier-8 contents-API preservation guard (the `if re.search(r"contents/",
    span): return span` line) so the UNCONDITIONAL body strip can be measured.

    Every other re.* call is delegated to the real module unchanged: the
    body-strip re.sub calls, and every DANGEROUS_PATTERNS match (those call
    .search on a *compiled* pattern object, not through mgc.re), are unaffected.
    This is the seam that lets the assumption-A survey (is contents the SOLE
    body-resident arm?) be re-proved through the REAL strip machinery rather
    than a re-implementation."""

    def search(self, pattern, string, *args, **kwargs):  # noqa: A003
        if pattern == r"contents/":
            return None
        return _real_re.search(pattern, string, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(_real_re, name)


@contextlib.contextmanager
def contents_guard_disabled():
    """Context manager: run the body inside an UNCONDITIONAL carrier-8 body strip
    (contents preservation guard off)."""
    saved = mgc.re
    mgc.re = _ContentsGuardDisabledRe()
    try:
        yield
    finally:
        mgc.re = saved


# ===========================================================================
# OP-CLASS 1 — remote-ref-delete (#1062a)
# ===========================================================================

# Every faithful single-ref-delete spelling: (command, expected target_ref).
# git deletion == EMPTY SOURCE (left of colon): `:ref`, `+:ref`, or `--delete`/
# `-d <ref>`. Covers the literal §9.B list PLUS the adversarial hunt: refs/heads/
# + refs/tags/ colon forms, lease `+:`, deep-slashed refs, flag reorder, quoted
# remote, explicit + implicit remote cross product. Each was verified to gate at
# the integrated HEAD with the EXACT target shown.
REMOTE_REF_DELETE_SPELLINGS = [
    # --- §9.B literal enumeration ---
    ("git push origin :feature", "feature"),
    ("git push origin +:feature", "feature"),
    ("git push origin --delete feature", "feature"),
    ("git push origin -d feature", "feature"),
    ("git push origin :refs/tags/v1", "refs/tags/v1"),
    ("git push -d origin ref", "ref"),
    ("git push :feature", "feature"),                  # implicit remote
    ("git push --delete feature", "feature"),          # implicit remote
    ("git push -d feature", "feature"),                # implicit remote
    ("git push --delete a b", "b"),                    # git grammar: repo=a, ref=b
    # --- adversarial hunt (beyond the literal list) ---
    ("git push origin :refs/heads/feature", "refs/heads/feature"),
    ("git push origin --delete refs/heads/feature", "refs/heads/feature"),
    ("git push origin +:refs/heads/feature", "refs/heads/feature"),
    ("git push origin :refs/tags/v1.0", "refs/tags/v1.0"),
    ("git push origin --delete refs/tags/v1.0", "refs/tags/v1.0"),
    ("git push origin -d refs/heads/feature", "refs/heads/feature"),
    ("git push :refs/heads/feature", "refs/heads/feature"),       # implicit remote
    ("git push --delete refs/tags/v1.0", "refs/tags/v1.0"),       # implicit remote
    ("git push +:feature", "feature"),                            # implicit force-delete
    ("git push -d origin feature", "feature"),                    # flag before remote
    ("git push origin :release/v1.2.3", "release/v1.2.3"),        # dotted ref
    ("git push origin --delete feat_x-y", "feat_x-y"),            # underscore/dash
    ('git push "origin" :feature', "feature"),                    # double-quoted remote
    ("git push origin :feature/sub/leaf", "feature/sub/leaf"),    # deep-slashed ref
    ("git push origin -o ci.skip :feature", "feature"),           # value-flag skip + real delete
]


class TestRemoteRefDeleteNoUnderBlock:
    """§9.B + adversarial hunt — every faithful single-ref-delete spelling MUST
    gate as remote-ref-delete with the EXACT target_ref. An un-enumerated faithful
    spelling that fails to gate would be a true under-block; this enumeration IS
    the completeness proof (it replaced the rejected literal-floor backstop)."""

    @pytest.mark.parametrize("cmd,target", REMOTE_REF_DELETE_SPELLINGS)
    def test_faithful_spelling_gates_with_exact_target(self, cmd, target):
        assert D(cmd) is True, f"UNDER-BLOCK: faithful delete not gated: {cmd!r}"
        assert OP(cmd) == "remote-ref-delete", f"misclassified: {cmd!r} -> {OP(cmd)}"
        # Exact-target assertion is the non-vacuity guarantee: a stub that always
        # returned is_dangerous=True would not bind the SPECIFIC ref the operator
        # approved, so mint==read target binding would be unproven.
        assert CTX(cmd).get("target_ref") == target, (
            f"target_ref mismatch for {cmd!r}: got {CTX(cmd).get('target_ref')!r}, want {target!r}"
        )


class TestRemoteRefDeleteNoOverBlock:
    """§9.C — faithful NON-delete push forms must NOT be classified as
    remote-ref-delete. The src:dst and quoted-colon (#1037) cases are the
    over-block class the design must avoid."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push origin feature",                 # plain push of a non-main branch
            "git push origin feat:feat",               # src:dst (non-delete update)
            "git push origin local:remote",            # src:dst both present
            "git push origin refs/heads/feature:",     # non-empty src, empty dst -> NOT a delete
            "git push origin +refs/heads/feature:",    # forced non-empty src, empty dst -> NOT a delete
            "git commit -m 'git push origin :feature'",  # delete literal in commit msg
        ],
    )
    def test_non_delete_form_not_classified_as_ref_delete(self, cmd):
        assert OP(cmd) != "remote-ref-delete", f"over-classified {cmd!r} as ref-delete"

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push origin main -o 'ci.message=cleanup :oldref'",  # quoted colon in push-option (#1037)
            "git push origin -o ':weird' main",                      # value-flag colon (#1037)
        ],
    )
    def test_quoted_colon_not_read_as_ref_delete(self, cmd):
        """#1037 brittleness class: a colon mentioned inside a quoted push-option
        value must not be mis-read as a ref-delete refspec. (These still gate as
        push-to-main on their own merits — that is asserted in the integration
        class; here we only assert they are not mis-bound as a ref-delete.)"""
        assert OP(cmd) != "remote-ref-delete"
        assert CTX(cmd).get("target_ref") in (None,), (
            f"{cmd!r} leaked a target_ref: {CTX(cmd).get('target_ref')!r}"
        )


class TestRemoteRefDeleteParityMintAndNonVacuity:
    """§9.A parity canary + the end-to-end mint authorize proof (the real #1064
    closure) + non-vacuity counter-mutations for remote-ref-delete."""

    @pytest.mark.parametrize("cmd,_target", REMOTE_REF_DELETE_SPELLINGS)
    def test_mint_equals_read_parity(self, cmd, _target):
        """is_dangerous_command (read floor) and detect_command_operation_type
        (mint classifier) must AGREE so a later edit cannot re-diverge them."""
        assert mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)

    def test_parity_canary_is_non_vacuous_under_detect_mutation(self, monkeypatch):
        """Counter-mutation proving the parity canary CAN fail: if the mint
        classifier were neutered to return None for a form the read floor still
        gates, the parity equality must break. A canary that stays green under
        this mutation would be vacuous."""
        cmd = "git push origin :feature"
        # sanity: green before mutation
        assert mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)
        monkeypatch.setattr(mgc, "detect_command_operation_type", lambda _c: None)
        # is_dangerous_command is an INDEPENDENT path (it does not call detect), so
        # it still gates True while the mutated detect returns None -> parity breaks.
        assert mgc.is_dangerous_command(cmd) is True
        assert (mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)) is False

    @pytest.mark.parametrize(
        "cmd,target",
        [
            ("git push origin :feature", "feature"),
            ("git push origin --delete refs/heads/feature", "refs/heads/feature"),
            ("git push :refs/tags/v1.0", "refs/tags/v1.0"),
        ],
    )
    def test_faithful_click_mints_and_authorizes_its_own_command(self, cmd, target):
        """End-to-end: a faithful ref-delete in an affirmative AUQ option mints a
        token whose target_ref re-matches the executed command -> AUTHORIZED. This
        is the #1064 closure (gated AND mintable), proved through the real mint
        path, not a read-gate alone."""
        result = mint(cmd)
        assert result.context is not None, f"did not mint (refusal={result.refusal_reason}) for {cmd!r}"
        assert result.context.get("operation_type") == "remote-ref-delete"
        assert result.context.get("target_ref") == target
        assert _target_value(result.context) is not None  # mint-side allow-list sees the target
        assert MATCH(token_from_ctx(result.context), cmd) is True

    def test_mint_authorize_proof_is_non_vacuous_wrong_target_refused(self):
        """Non-vacuity for the authorize proof: a token whose target_ref is WRONG
        must NOT authorize the command. If MATCH ignored the target, the green
        authorize assertion above would be meaningless."""
        cmd = "git push origin :feature"
        assert MATCH(token("remote-ref-delete", target_ref="feature"), cmd) is True
        assert MATCH(token("remote-ref-delete", target_ref="other"), cmd) is False

    def test_cross_op_token_does_not_authorize_ref_delete(self):
        """A force-push token must NOT authorize a remote-ref-delete of the same
        ref (distinct op-classes), and vice-versa — the lead-Q1 distinct-op-class
        guarantee."""
        assert MATCH(token("force-push", target_ref="feature"), "git push origin :feature") is False
        assert MATCH(token("remote-ref-delete", target_ref="feature"), "git push --force origin feature") is False


# ===========================================================================
# OP-CLASS 2 — remote-mass-delete (#1062b)
# ===========================================================================

# Faithful mass forms: (command, expected mass_target). mass_target binds the
# destructive IDENTITY (sorted mass-flags @ remote-or-implicit [# sorted refspecs]),
# NOT the whole command.
REMOTE_MASS_DELETE_SPELLINGS = [
    ("git push --mirror origin", "--mirror@origin"),
    ("git push --mirror", f"--mirror@{IMPLICIT}"),                 # implicit remote
    ("git push --prune origin", "--prune@origin"),
    ("git push --prune origin refs/heads/main", "--prune@origin#refs/heads/main"),
    ("git push origin --delete a b", "--delete@origin#a,b"),       # multi-ref
    ("git push origin :a :b", "--delete@origin#a,b"),              # multi colon
    ("git push origin --mirror", "--mirror@origin"),               # flag after remote
    ("git push origin --prune", "--prune@origin"),                 # flag after remote
    ("git push origin --delete a b c", "--delete@origin#a,b,c"),   # three-ref
    ('git push --mirror "origin"', "--mirror@origin"),             # quoted remote
]


class TestRemoteMassDeleteNoUnderBlock:
    """§9.H.b/c/d — every faithful mass form gates as remote-mass-delete with the
    EXACT mass_target, AND the mint-side allow-list (_target_value) sees it
    (#1064-impossible canary: recognition and mintability are the same predicate)."""

    @pytest.mark.parametrize("cmd,mass_target", REMOTE_MASS_DELETE_SPELLINGS)
    def test_faithful_mass_form_gates_with_exact_mass_target(self, cmd, mass_target):
        assert D(cmd) is True, f"UNDER-BLOCK: faithful mass form not gated: {cmd!r}"
        assert OP(cmd) == "remote-mass-delete", f"misclassified: {cmd!r} -> {OP(cmd)}"
        assert CTX(cmd).get("mass_target") == mass_target, (
            f"mass_target mismatch for {cmd!r}: got {CTX(cmd).get('mass_target')!r}, want {mass_target!r}"
        )

    @pytest.mark.parametrize("cmd,_mt", REMOTE_MASS_DELETE_SPELLINGS)
    def test_recognition_implies_mintable_no_1064(self, cmd, _mt):
        """#1064-impossible: whenever a mass form is recognized, the mint-side
        _target_value MUST be populated — recognition <=> mintability."""
        ctx = CTX(cmd)
        assert ctx.get("mass_target") is not None
        assert _target_value(ctx) is not None

    def test_implicit_remote_is_mintable(self):
        """§9.H.b — the target-less implicit-remote mass form mints (no #1064)."""
        cmd = "git push --mirror"
        assert D(cmd) is True
        assert OP(cmd) == "remote-mass-delete"
        assert CTX(cmd).get("mass_target") == f"--mirror@{IMPLICIT}"


class TestRemoteMassDeleteCrossAuthDistinctness:
    """§9.H.a — no-cross-auth: distinct destructive identities mint distinct
    tokens, and a token for one mass identity does NOT authorize another (the
    lesser->greater closure that the rejected coarse sentinel would have reopened)."""

    def test_distinct_mass_identities_are_distinct_targets(self):
        assert CTX("git push --prune origin")["mass_target"] != CTX("git push --mirror origin")["mass_target"]
        assert CTX("git push --mirror origin")["mass_target"] != CTX("git push --mirror origin2")["mass_target"]
        assert CTX("git push origin --delete a b")["mass_target"] != CTX("git push origin --delete a c")["mass_target"]

    def test_prune_token_does_not_authorize_mirror(self):
        tok = token("remote-mass-delete", mass_target="--prune@origin")
        assert MATCH(tok, "git push --prune origin") is True            # authorizes its own
        assert MATCH(tok, "git push --mirror origin") is False          # not a different identity

    def test_mirror_token_does_not_authorize_prune(self):
        tok = token("remote-mass-delete", mass_target="--mirror@origin")
        assert MATCH(tok, "git push --mirror origin") is True
        assert MATCH(tok, "git push --prune origin") is False

    def test_distinct_remote_does_not_cross_authorize(self):
        tok = token("remote-mass-delete", mass_target="--mirror@origin")
        assert MATCH(tok, "git push --mirror origin2") is False

    def test_refspec_set_closure_a_b_does_not_authorize_a_c(self):
        tok = token("remote-mass-delete", mass_target="--delete@origin#a,b")
        assert MATCH(tok, "git push origin --delete a b") is True
        assert MATCH(tok, "git push origin --delete a c") is False


class TestRemoteMassDeleteBoundary:
    """§9.H.e — the single-vs-multi --delete boundary. EXACTLY one op-class per
    command; the git-grammar rationale (first positional == repository) is pinned
    in the assertions so the implicit-remote 2-token case is not mistaken for a bug."""

    @pytest.mark.parametrize(
        "cmd,expected_op,expected_target",
        [
            # 1 ref -> remote-ref-delete
            ("git push origin --delete a", "remote-ref-delete", "a"),
            # git grammar: `git push --delete a b` => repo=a, ref=b => SINGLE delete
            ("git push --delete a b", "remote-ref-delete", "b"),
            # explicit remote + 2 refs => MULTI => remote-mass-delete
            ("git push origin --delete a b", "remote-mass-delete", "--delete@origin#a,b"),
            ("git push --mirror origin", "remote-mass-delete", "--mirror@origin"),
        ],
    )
    def test_single_vs_multi_delete_routing(self, cmd, expected_op, expected_target):
        assert OP(cmd) == expected_op, f"{cmd!r} -> {OP(cmd)}, want {expected_op}"
        ctx = CTX(cmd)
        if expected_op == "remote-ref-delete":
            assert ctx.get("target_ref") == expected_target
        else:
            assert ctx.get("mass_target") == expected_target

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push origin --delete a",
            "git push --delete a b",
            "git push origin --delete a b",
            "git push --mirror origin",
            "git push origin :a :b",
        ],
    )
    def test_no_double_classification(self, cmd):
        """No command may populate BOTH target_ref and mass_target — exactly one
        op-class binds (the single-ref-extractability discriminator)."""
        ctx = CTX(cmd)
        assert not (ctx.get("target_ref") and ctx.get("mass_target")), (
            f"double-classified {cmd!r}: target_ref={ctx.get('target_ref')!r} mass_target={ctx.get('mass_target')!r}"
        )


class TestRemoteMassDeleteParityMintAndNonVacuity:
    """§9.H.d/f — parity canary extended to remote-mass-delete + the end-to-end
    mint authorize proof + non-vacuity."""

    @pytest.mark.parametrize("cmd,_mt", REMOTE_MASS_DELETE_SPELLINGS)
    def test_mint_equals_read_parity(self, cmd, _mt):
        assert mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)

    @pytest.mark.parametrize(
        "cmd,mass_target",
        [
            ("git push --mirror origin", "--mirror@origin"),
            ("git push --mirror", f"--mirror@{IMPLICIT}"),
            ("git push origin --delete a b", "--delete@origin#a,b"),
        ],
    )
    def test_faithful_click_mints_and_authorizes(self, cmd, mass_target):
        result = mint(cmd)
        assert result.context is not None, f"did not mint (refusal={result.refusal_reason}) for {cmd!r}"
        assert result.context.get("operation_type") == "remote-mass-delete"
        assert result.context.get("mass_target") == mass_target
        assert MATCH(token_from_ctx(result.context), cmd) is True

    def test_mint_authorize_proof_is_non_vacuous_wrong_target_refused(self):
        cmd = "git push --mirror origin"
        assert MATCH(token("remote-mass-delete", mass_target="--mirror@origin"), cmd) is True
        assert MATCH(token("remote-mass-delete", mass_target="--prune@origin"), cmd) is False


class TestRemoteMassDeleteNoOverBlock:
    """Negatives — faithful non-mass forms and a mass literal carried in a commit
    message (carrier-5 strips it) must stay UNGATED."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push origin feature",
            "git push origin feat:feat",
            "git commit -m 'git push --mirror origin'",
        ],
    )
    def test_non_mass_form_not_classified_as_mass(self, cmd):
        assert OP(cmd) != "remote-mass-delete"

    def test_commit_msg_mass_literal_is_not_dangerous(self):
        assert D("git commit -m 'git push --mirror origin'") is False


# ===========================================================================
# OP-CLASS 3 — branch-protection (#1063)
# ===========================================================================

# Per-client templates; {M} is the HTTP method. Host-agnostic: api and non-api
# (Enterprise) hosts both gate.
PROTECTION_CLIENTS = {
    "gh-api": "gh api -X {M} repos/o/r/branches/main/protection",
    "gh-api-method": "gh api --method {M} repos/o/r/branches/main/protection",
    "curl-api-host": "curl -X {M} https://api.github.com/repos/o/r/branches/main/protection",
    "curl-enterprise-host": "curl -X {M} https://git.example.com/repos/o/r/branches/main/protection",
    "wget": "wget --method={M} https://git.example.com/repos/o/r/branches/main/protection",
}
PROTECTION_GATED_METHODS = ["DELETE", "PUT", "PATCH"]


class TestBranchProtectionGate:
    """§9.E — per-method x client gate, host-agnostic, protected_branch extracted +
    mintable, mint==read parity."""

    @pytest.mark.parametrize("method", PROTECTION_GATED_METHODS)
    @pytest.mark.parametrize("client", list(PROTECTION_CLIENTS))
    def test_weakening_method_gates_per_client(self, method, client):
        cmd = PROTECTION_CLIENTS[client].format(M=method)
        assert D(cmd) is True, f"UNDER-BLOCK: {client} {method} not gated: {cmd!r}"
        assert OP(cmd) == "branch-protection"
        assert CTX(cmd).get("protected_branch") == "main"
        assert _target_value(CTX(cmd)) is not None  # mintable
        # parity
        assert mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)


class TestBranchProtectionPostNotGated:
    """§9.E strengthening direction — POST ENABLES protection sub-features, so it
    must NOT gate (gating it would over-block)."""

    @pytest.mark.parametrize("client", list(PROTECTION_CLIENTS))
    def test_post_does_not_gate(self, client):
        cmd = PROTECTION_CLIENTS[client].format(M="POST")
        assert OP(cmd) != "branch-protection", f"POST over-classified: {cmd!r}"
        assert D(cmd) is False, f"POST over-blocked (strengthening): {cmd!r}"


class TestBranchProtectionSlashedAndMintAndNonVacuity:
    """Slashed-branch extraction + the end-to-end mint authorize proof + cross-op
    closure + non-vacuity counter-mutation."""

    @pytest.mark.parametrize(
        "cmd,branch",
        [
            ("gh api -X DELETE repos/o/r/branches/feature/x/protection", "feature/x"),
            ("gh api -X PUT repos/o/r/branches/release/1.2/x/protection", "release/1.2/x"),
            ("curl -X PATCH https://git.example.com/repos/o/r/branches/dev/protection", "dev"),
        ],
    )
    def test_slashed_and_simple_branch_extracted(self, cmd, branch):
        assert D(cmd) is True
        assert CTX(cmd).get("protected_branch") == branch

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api -X DELETE repos/o/r/branches/main/protection",
            "gh api -X PUT repos/o/r/branches/release/x/protection",
        ],
    )
    def test_faithful_click_mints_and_authorizes(self, cmd):
        result = mint(cmd)
        assert result.context is not None, f"did not mint (refusal={result.refusal_reason}) for {cmd!r}"
        assert result.context.get("operation_type") == "branch-protection"
        assert result.context.get("protected_branch") == CTX(cmd).get("protected_branch")
        assert MATCH(token_from_ctx(result.context), cmd) is True

    def test_mint_authorize_proof_non_vacuous_wrong_branch_refused(self):
        cmd = "gh api -X DELETE repos/o/r/branches/main/protection"
        assert MATCH(token("branch-protection", protected_branch="main"), cmd) is True
        assert MATCH(token("branch-protection", protected_branch="other"), cmd) is False

    def test_cross_op_closure(self):
        """A branch-protection token must NOT authorize a CLI branch-delete of the
        same name (distinct op-classes)."""
        tok = token("branch-protection", protected_branch="main")
        assert MATCH(tok, "gh api -X DELETE repos/o/r/branches/main/protection") is True
        assert MATCH(tok, "git branch -D main") is False


# ===========================================================================
# #1061 — host-agnostic curl ref-mutation: gate + mint + authorize + parity
# ===========================================================================


class TestHostAgnosticCurlRefMutationMintAndParity:
    """#1061 — the curl ref-mutation read floor is now host-agnostic (the literal
    `.*api.*` was dropped from the curl git/refs DANGEROUS_PATTERNS arms), so an
    HONEST ref-mutation against a non-api Enterprise/proxy host now ROUTES THROUGH
    APPROVAL instead of bypassing.

    This class pins the FULL "routes through approval" property for the
    host-agnostic-widened forms — not merely the read gate (covered by the
    carrier-8 PATH-vs-BODY positives), but that each MINTS a token AND the minted
    token AUTHORIZES its own command, with mint==read parity. That directly
    satisfies the "every gated op must mint" hard constraint for #1061 rather than
    relying on transitive coverage of the pre-existing API-ref op. The underlying
    op is the pre-existing API ref-mutation (DELETE -> branch-delete; PATCH/PUT ->
    force-push); #1061 brings the non-api-host READ floor to parity with the
    already-host-agnostic mint classifier. (curl commands must be backtick-wrapped
    in the AUQ option text to mint — pre-existing locate_command_regions substrate
    behavior; the mint() helper does this.)"""

    @pytest.mark.parametrize(
        "cmd,expected_op",
        [
            # non-api Enterprise / proxy hosts (no `api` substring in the URL)
            ("curl -X DELETE https://git.example.com/repos/o/r/git/refs/heads/y", "branch-delete"),
            ("curl --request DELETE https://git.internal.corp/repos/o/r/git/refs/heads/feature", "branch-delete"),
            ("curl -X PATCH https://git.example.com/repos/o/r/git/refs/heads/y", "force-push"),
        ],
    )
    def test_non_api_host_ref_mutation_gates_classifies_mints_and_authorizes(self, cmd, expected_op):
        # read gate (the host-agnostic widening)
        assert D(cmd) is True, f"UNDER-BLOCK: non-api-host ref-mutation not gated: {cmd!r}"
        assert OP(cmd) == expected_op
        # mint==read parity
        assert mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)
        # mints + authorizes its own command (the #1064 "every gated op mints" closure)
        result = mint(cmd)
        assert result.context is not None, f"did not mint (refusal={result.refusal_reason}) for {cmd!r}"
        assert result.context.get("operation_type") == expected_op
        assert MATCH(token_from_ctx(result.context), cmd) is True

    def test_widening_is_a_superset_api_host_still_gates_and_mints(self):
        """Control: the api-host form (which gated pre-#1061 too) STILL gates +
        mints + authorizes — proving #1061 is a strict superset (non-api host
        ADDED), not a swap that could have dropped the api-host coverage."""
        cmd = "curl -X DELETE https://api.github.com/repos/o/r/git/refs/heads/y"
        assert D(cmd) is True
        result = mint(cmd)
        assert result.context is not None, f"did not mint (refusal={result.refusal_reason})"
        assert MATCH(token_from_ctx(result.context), cmd) is True


# ===========================================================================
# CARRIER-8 — PATH-vs-BODY invariant (#1037 over-block mitigation, §9.D/E)
# ===========================================================================


class TestCarrier8PathVsBody:
    """§9.D/E — carrier-8 strips the HTTP-client data BODY but never the URL PATH,
    so a faithful path-resident target STILL gates after the strip (zero
    under-block), while a benign command merely MENTIONING a sensitive path inside
    a quoted body is NOT over-blocked (#1037 FP killed)."""

    @pytest.mark.parametrize(
        "cmd",
        [
            # ref in PATH, with and without a body -> STILL gates after carrier-8
            "curl -X DELETE https://git.example.com/repos/o/r/git/refs/heads/y",
            "curl -X DELETE https://git.example.com/repos/o/r/git/refs/heads/y -d '{}'",
            "curl -X DELETE https://git.example.com/repos/o/r/git/refs/heads/y -d 'unrelated body'",
            "curl -f -X DELETE https://git.example.com/repos/o/r/git/refs/heads/y",  # -f boolean not mis-stripped
            # protection target in PATH, with a field/body mention -> STILL gates
            "gh api -X DELETE repos/o/r/branches/main/protection --field reason='see branches/x/protection'",
            "curl -X DELETE https://git.example.com/repos/o/r/branches/main/protection -d 'note: branches/x/protection'",
        ],
    )
    def test_path_resident_target_survives_body_strip(self, cmd):
        assert D(cmd) is True, f"UNDER-BLOCK: path-resident target lost to body strip: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            # #1037 FP: git/refs / protection MENTIONED only in a quoted body -> UNGATED
            "curl -X POST https://example.com/log -d 'msg=touched git/refs/heads/x'",
            'curl -X POST https://example.com/log -d "msg=touched git/refs/heads/x"',
            "wget --method=POST https://example.com/log --body-data 'x git/refs y'",
            "gh api -X DELETE repos/o/r/issues/5 --field note='see git/refs/heads/x'",
            "gh api -X DELETE repos/o/r/issues/5 -f body='touch git/refs/heads/x'",
            "gh api -X POST repos/o/r/issues/5 --field note='touch branches/x/protection now'",
            "curl -X POST https://example.com/log -d 'msg=branches/main/protection'",
        ],
    )
    def test_body_only_mention_is_not_over_blocked(self, cmd):
        assert D(cmd) is False, f"OVER-BLOCK (#1037): body-only mention gated: {cmd!r}"

    def test_carrier8_keeps_flag_strips_body_value(self):
        """The flag token survives (implicit-POST lookaheads must still fire);
        only the body VALUE is removed."""
        stripped = mgc._strip_non_executable_content(
            "curl -X POST https://example.com/log -d 'git/refs/x'"
        )
        assert "-d " in stripped
        assert "git/refs" not in stripped

    def test_carrier8_does_not_strip_git_push_ref(self):
        """carrier-8 is HTTP-client-scoped: a `git push -d <ref>` (git surface, not
        an HTTP client) must keep its ref."""
        stripped = mgc._strip_non_executable_content("git push -d feature")
        assert "feature" in stripped


# ===========================================================================
# CONTENTS-API regression + the assumption-A body-resident survey (§9.D)
# ===========================================================================


class TestContentsApiRegression:
    """§9.D — the body-resident contents-API arm. carrier-8 strips request bodies,
    but the contents arm reads its destructive target (the main/master branch) from
    the BODY, so a per-span contents preservation guard keeps it gating. These are
    the existing TestContentsAPI forms re-pinned AFTER carrier-8 landed."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api -X PUT repos/owner/repo/contents/README.md -f branch=main -f sha=abc",
            "gh api -X PUT repos/owner/repo/contents/README.md -f branch=master",
            "gh api -X PATCH repos/owner/repo/contents/README.md -f branch=main",
            'curl -X PUT https://api.github.com/repos/o/r/contents/README.md -d \'{"branch":"main","sha":"abc"}\'',
            'curl -X PUT https://api.github.com/repos/o/r/contents/README.md -d \'{"branch":"master"}\'',
        ],
    )
    def test_contents_forms_still_gate_after_carrier8(self, cmd):
        assert D(cmd) is True, f"REGRESSION: contents form no longer gates: {cmd!r}"

    def test_strip_preserves_contents_body_signal(self):
        """The per-span guard keeps the body-resident main/master signal: stripping
        a contents command preserves `main`."""
        stripped = mgc._strip_non_executable_content(
            'curl -X PUT https://api.github.com/repos/o/r/contents/README.md -d \'{"branch":"main"}\''
        )
        assert "main" in stripped

    def test_discriminating_negative_contents_guard_is_load_bearing(self):
        """DISCRIMINATING NEGATIVE (non-vacuity for the guard): with the contents
        preservation guard conceptually removed (unconditional body strip), the
        curl contents form — whose main/master signal is in a QUOTED body value —
        regresses True->False. This proves the guard is load-bearing: without it,
        a faithful body-resident contents mutation would UNDER-BLOCK.

        (Refinement surfaced by independent re-verification: only the
        carrier-8-strippable forms regress — i.e. QUOTED body values. The existing
        gh-api forms use an UNQUOTED `-f branch=main`, which carrier-8 never strips,
        so they do not regress under guard-removal; the curl form, with a quoted
        JSON body, is the true body-resident-strippable case.)"""
        curl_contents = 'curl -X PUT https://api.github.com/repos/o/r/contents/README.md -d \'{"branch":"main"}\''
        # guard ON: gates
        assert D(curl_contents) is True
        # guard OFF (unconditional strip): regresses -> proves the guard matters
        with contents_guard_disabled():
            assert D(curl_contents) is False
        # guard restored (control): gates again
        assert D(curl_contents) is True


class TestBodyResidentArmSurvey:
    """Independent re-verification of assumption (A): is contents-API the SOLE
    gated arm whose destructive target is BODY-resident? Method (NOT trusting the
    architect's discriminating-negative verdict): enumerate one faithful command
    per API arm and apply the UNCONDITIONAL body strip (contents guard disabled).
    The load-bearing invariant is that NO non-contents API arm regresses — every
    other arm's target is PATH-resident and survives the strip. This is what proves
    the per-span contents guard is both load-bearing AND sufficient (no second
    body-resident arm needs a matching guard)."""

    # One faithful destructive command per NON-contents API arm. Each carries a
    # body flag so the strip has something to remove; each keeps its target in the
    # URL PATH. (Verified to stay True under the unconditional strip.)
    NON_CONTENTS_API_ARMS = [
        "gh api -X PUT repos/o/r/pulls/5/merge -f sha=abc",
        'curl -X PUT https://api.github.com/repos/o/r/pulls/5/merge -d \'{"sha":"abc"}\'',
        "gh api -X DELETE repos/o/r/git/refs/heads/y -f note=x",
        "curl -X DELETE https://git.example.com/repos/o/r/git/refs/heads/y -d 'note=x'",
        "gh api -X PATCH repos/o/r/git/refs/heads/y -f sha=abc",
        "curl -X PATCH https://git.example.com/repos/o/r/git/refs/heads/y -d 'sha=abc'",
        "gh api repos/o/r/git/refs/heads/y -f sha=abc",                       # implicit POST
        "curl https://git.example.com/repos/o/r/git/refs/heads/y -d 'sha=abc'",  # implicit POST
        "gh api -X DELETE repos/o/r/branches/main/protection -f note=x",
        "curl -X DELETE https://git.example.com/repos/o/r/branches/main/protection -d 'note=x'",
        "wget --method=PUT https://git.example.com/repos/o/r/branches/main/protection --body-data 'x'",
        "wget --method=DELETE https://git.example.com/repos/o/r/git/refs/heads/y --body-data 'x'",
    ]

    @pytest.mark.parametrize("cmd", NON_CONTENTS_API_ARMS)
    def test_non_contents_arm_is_path_resident_survives_unconditional_strip(self, cmd):
        # baseline: gates
        assert D(cmd) is True, f"baseline: {cmd!r} should gate"
        # under the UNCONDITIONAL strip it must STILL gate (target is path-resident)
        with contents_guard_disabled():
            assert D(cmd) is True, (
                f"SECOND BODY-RESIDENT ARM: {cmd!r} regressed under an unconditional "
                f"body strip -> carrier-8 would under-block it (assumption-A violated)"
            )

    def test_only_contents_is_body_resident(self):
        """The asymmetry proof: under the unconditional strip, the contents arm
        (curl, quoted body) DOES regress while every non-contents arm survives ->
        contents is the sole body-resident arm and the single per-span guard is
        sufficient."""
        contents = 'curl -X PUT https://api.github.com/repos/o/r/contents/README.md -d \'{"branch":"main"}\''
        with contents_guard_disabled():
            contents_regressed = D(contents) is False
            non_contents_survived = all(D(c) for c in self.NON_CONTENTS_API_ARMS)
        assert contents_regressed, "contents arm did not regress under unconditional strip"
        assert non_contents_survived, "a non-contents arm regressed -> a second body-resident arm exists"


# ===========================================================================
# OUT-of-charter negatives + integration + accepted-grain / awareness pins
# ===========================================================================


class TestOutOfCharterNegatives:
    """§9.F — the #1063 charter boundary. Repo/release/api-repo deletion is OUT of
    charter and MUST stay ungated; a future widening that catches these is a scope
    violation."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh repo delete owner/repo",
            "gh release delete v1",
            "gh api -X DELETE repos/o/r",
        ],
    )
    def test_out_of_charter_stays_ungated(self, cmd):
        assert D(cmd) is False, f"SCOPE VIOLATION: out-of-charter form gated: {cmd!r}"


class TestStaleSnapshotIntegration:
    """CODE integration note — the deferred->gated cross-group transition. In the
    #1062a-only state `git push origin --delete a b` and `git push --mirror origin`
    were is_dangerous=False; after #1062b they gate as remote-mass-delete. Pin the
    INTEGRATED-HEAD behavior so a stale pre-integration negative cannot creep back."""

    @pytest.mark.parametrize(
        "cmd,mass_target",
        [
            ("git push origin --delete a b", "--delete@origin#a,b"),
            ("git push --mirror origin", "--mirror@origin"),
        ],
    )
    def test_formerly_deferred_forms_now_gate_as_mass(self, cmd, mass_target):
        assert D(cmd) is True
        assert OP(cmd) == "remote-mass-delete"
        assert CTX(cmd).get("mass_target") == mass_target

    def test_push_to_main_with_quoted_colon_gates_as_push_to_main(self):
        """The quoted-colon push-option form is NOT a ref-delete but IS dangerous on
        its own merits (push-to-main) — the #1037 quoted colon is inert, the
        push-to-main classification stands."""
        cmd = "git push origin main -o 'ci.message=cleanup :oldref'"
        assert D(cmd) is True
        assert OP(cmd) == "push-to-main"
        assert OP(cmd) != "remote-ref-delete"


class TestAcceptedGrainAndAwarenessPins:
    """§10 accepted-grain decisions + the auditor awareness pins. These document
    DELIBERATE, ratified boundaries — they are NOT under-blocks, and pinning them
    prevents a future reader from mistaking the boundary for a bug."""

    def test_cross_remote_same_named_single_ref_delete_shares_token_accepted_grain(self):
        """§10 target-grain asymmetry: remote-ref-delete binds ref-ONLY (no remote,
        per the force-push target_ref precedent), so a token minted for one remote
        authorizes the SAME-named single-ref delete on a DIFFERENT remote within the
        single-use window. This is an ACCEPTED honest-mistake grain by explicit
        decision (the operator approved "delete ref feature"; the remote differs) —
        documented here as accepted, NOT an under-block."""
        tok = token("remote-ref-delete", target_ref="feature")
        assert MATCH(tok, "git push origin :feature") is True
        assert MATCH(tok, "git push upstream :feature") is True  # ACCEPTED grain (ref-only bind)

    def test_quoted_gh_api_field_value_collides_with_preexisting_var_strip(self):
        """AWARENESS PIN (pre-existing, OUT-of-scope): a gh-api field value written
        as a QUOTED `key='value'` (e.g. `-f branch='main'`) collides with the
        pre-existing shell variable-assignment laundering strip (which rewrites
        `name='value'` -> `name=STRIPPED`), so a QUOTED-branch contents form is
        is_dangerous=False. This is NOT introduced by carrier-8 (the var-strip is
        byte-identical pre/post-PR and produces a bareword `=STRIPPED`, unlike
        carrier-8's re-quoted `'STRIPPED'`), and var-laundering is explicitly OUT
        OF SCOPE per the threat model. The faithful/canonical gh-api form uses an
        UNQUOTED `-f branch=main`, which gates correctly."""
        # canonical unquoted form: gates
        assert D("gh api -X PUT repos/o/r/contents/README.md -f branch=main") is True
        # quoted-value form collides with the pre-existing var-strip (out of scope)
        assert D("gh api -X PUT repos/o/r/contents/README.md -f branch='main'") is False

    def test_bare_curl_wget_non_mint_is_failsafe_over_block(self):
        """AUDITOR TEST-NOTE 1 (fail-safe awareness): curl/wget API forms MINT only
        when the command is QUOTED/backtick-wrapped in the AUQ option text
        (pre-existing locate_command_regions substrate behavior). A BARE (unwrapped)
        curl/wget option text does not mint — a fail-safe over-block characteristic,
        accepted. gh-api mints in every framing (asserted via the backtick-wrapped
        mint() helper elsewhere)."""
        bare = {
            "question": "Proceed?",
            "options": [{
                "label": "Yes, do it",
                # NOTE: no backticks around the command -> bare option text
                "description": "Run curl -X DELETE https://git.example.com/repos/o/r/git/refs/heads/y now",
            }],
            "multiSelect": False,
        }
        result = _mint_context_from_bundle([bare], {"Proceed?": "Yes, do it"})
        # fail-safe: no mint from bare curl text (accepted over-block, not an under-block)
        assert result.context is None

    def test_protection_command_with_gitrefs_body_still_gates(self):
        """AUDITOR TEST-NOTE 2 (awareness): a branch-protection command whose data
        BODY literally contains `git/refs/heads/<x>` may be detect-classified by the
        pre-existing git/refs body arm, but it STILL gates (mint==read consistent ->
        self-authorizes). A faithful CLEAN-body protection command binds correctly
        as branch-protection. Either way is_dangerous holds — strict improvement
        over base; the collision needs a crafted body (out of honest-mistake scope)."""
        crafted = "gh api -X DELETE repos/o/r/branches/main/protection -f note='git/refs/heads/x'"
        assert D(crafted) is True  # gates regardless of the body collision
        clean = "gh api -X DELETE repos/o/r/branches/main/protection"
        assert D(clean) is True
        assert OP(clean) == "branch-protection"
        assert CTX(clean).get("protected_branch") == "main"


# ===========================================================================
# REVIEW-CYCLE-1 REMEDIATION — over-block fixes locked in (mint widened to match
# the read floor, NO read-narrowing) + the accepted conservative-recognition
# limitation pinned as INTENTIONAL forward-protection.
# ===========================================================================


class TestOverBlockGoneRegression:
    """The review-cycle-1 over-block fixes ripped out over-blocks by WIDENING the
    mint to match the read floor (never narrowing detection into a new under-block).
    Each test asserts a FAITHFUL form now routes correctly (gates AND, where a mint
    is expected, mints AND the token authorizes its own command) so a regression
    that re-introduces the over-block turns these red. Non-vacuity is intrinsic: the
    mint+authorize assertions only hold post-fix (pre-fix these were either
    mislabeled-dangerous or gated-but-unmintable), plus the case-sensitive
    counter-mutation below + the literal-arm contrast in the sibling class."""

    # --- cross-leg leak: a benign first-leg push is no longer mislabeled a delete ---

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push origin feature && git branch -d old",   # && + safe (-d, merged-only) branch delete
            "git push origin feature ; git branch -d old",    # ; variant
            "git push origin feature | grep done",            # pipe-to-viewer variant
        ],
    )
    def test_benign_push_not_mislabeled_as_delete(self, cmd):
        assert D(cmd) is False, f"OVER-BLOCK regressed: benign push mislabeled dangerous: {cmd!r}"

    def test_single_first_leg_delete_still_gates_control(self):
        """Non-vacuity control for the cross-leg cases: a real single first-leg
        ref-delete STILL gates, so the D=False assertions above are discriminating,
        not a blanket 'compound is always safe'."""
        assert D("git push origin --delete main") is True
        assert OP("git push origin --delete main") == "remote-ref-delete"

    # --- lowercase / mixed-case HTTP methods now MINT (mint widened to IGNORECASE) ---

    @pytest.mark.parametrize(
        "cmd,expected_op,target_key,target_val",
        [
            ("gh api -X delete repos/o/r/git/refs/heads/y", "branch-delete", "branch", "y"),
            ("gh api --method delete repos/o/r/git/refs/heads/y", "branch-delete", "branch", "y"),
            ("gh api -X Delete repos/o/r/branches/main/protection", "branch-protection", "protected_branch", "main"),
        ],
    )
    def test_lowercase_method_gh_api_gates_mints_and_authorizes(self, cmd, expected_op, target_key, target_val):
        assert D(cmd) is True
        assert OP(cmd) == expected_op
        result = mint(cmd)
        assert result.context is not None, f"did not mint (refusal={result.refusal_reason}) for {cmd!r}"
        assert result.context.get("operation_type") == expected_op
        assert result.context.get(target_key) == target_val
        assert MATCH(token_from_ctx(result.context), cmd) is True
        # parity equality now holds for the mint-widened lowercase form
        assert mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)

    def test_curl_lowercase_method_protection_mints_and_authorizes(self):
        """curl + lowercase method + protection PATH gates+mints+authorizes (curl must
        be backtick-wrapped in the option text to mint — the mint() helper does)."""
        cmd = "curl -X put https://git.example.com/repos/o/r/branches/main/protection"
        assert D(cmd) is True
        assert OP(cmd) == "branch-protection"
        result = mint(cmd)
        assert result.context is not None, f"did not mint (refusal={result.refusal_reason})"
        assert result.context.get("protected_branch") == "main"
        assert MATCH(token_from_ctx(result.context), cmd) is True

    # --- gh global flag before `api` (gh -R o/r api ...) now MINTS ---

    def test_gh_global_flag_gates_mints_and_authorizes(self):
        cmd = "gh -R o/r api -X DELETE repos/o/r/branches/main/protection"
        assert D(cmd) is True
        assert OP(cmd) == "branch-protection"
        result = mint(cmd)
        assert result.context is not None, f"did not mint (refusal={result.refusal_reason})"
        assert result.context.get("protected_branch") == "main"
        assert MATCH(token_from_ctx(result.context), cmd) is True

    def test_gh_global_flag_body_mention_not_over_blocked(self):
        """carrier-8 under the gh -R framing: a git/refs MENTION in a -f field body is
        NOT over-blocked, while the faithful path-resident protection delete (same
        gh -R framing) STILL gates — the PATH-vs-BODY invariant holds for gh -R too."""
        body_mention = "gh -R o/r api -X POST repos/o/r/issues/5 -f note='see git/refs/heads/x'"
        assert D(body_mention) is False, f"OVER-BLOCK (#1037): gh -R body mention gated: {body_mention!r}"
        faithful = "gh -R o/r api -X DELETE repos/o/r/branches/main/protection"
        assert D(faithful) is True

    # --- non-vacuity: in-memory counter-mutation (NOT git-revert; the shared worktree
    #     holds the architect's staged doc WIP, so a checkout-revert would destroy it) ---

    def test_lowercase_parity_is_non_vacuous_under_case_sensitive_mutation(self, monkeypatch):
        """Simulate the PRE-FIX case-sensitive detect (the bug) by neutering detect to
        None for the lowercase form, and assert the mint==read parity equality BREAKS.
        Proves the widened-form parity assertions are coupled to the mint-widening fix
        and would go red if it were reverted. In-memory (monkeypatch) by design — a
        git-revert here would clobber a peer's staged uncommitted edit in the shared
        worktree."""
        cmd = "gh api -X delete repos/o/r/git/refs/heads/y"
        # green before mutation (fix present)
        assert mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)
        monkeypatch.setattr(mgc, "detect_command_operation_type", lambda _c: None)
        # read floor still gates (independent path); mutated detect None -> parity breaks
        assert mgc.is_dangerous_command(cmd) is True
        assert (mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)) is False


class TestAcceptedRecognitionLimitationPins:
    """FORWARD-PROTECTION pins for the ACCEPTED conservative-recognition limitation
    (the review-cycle-1 SECURITY-HALT disposition). These forms run UNGATED BY
    DESIGN, and these tests assert that ON PURPOSE — they are the executable tripwire
    so a future maintainer who 'hardens' recognition into a match-anywhere / per-leg
    scan (which would re-block faithful clicks) sees these flip RED and STOPS.

    THE PRINCIPLE (do NOT 'fix' these): the git-push remote-ref-delete (:ref /
    --delete / -d) and mass-delete (--mirror / --prune / multi-ref) forms need a
    positional, quote-aware parse, so their recognition is ANCHORED to the FIRST
    executable leg and does NOT chase the op into NON-FIRST compound legs. Chasing it
    requires a match-anywhere scan that fires on a quoted :ref / --mirror mention in a
    benign leg = an over-block of a faithful click, which is WRONG BY DEFINITION
    (worse than missing a buried op). The fix for any over-block WIDENS the mint,
    never narrows detection into a new under-block.
    """

    @pytest.mark.parametrize(
        "cmd",
        [
            "cd /repo && git push origin --delete main",   # ref-delete in a non-first leg
            "git fetch && git push --mirror origin",        # mass-delete in a non-first leg
            "NOTE=x ; git push origin :main",               # colon-delete after an assignment leg
        ],
    )
    def test_non_first_leg_push_delete_ungated_by_design(self, cmd):
        """ACCEPTED LIMITATION — do NOT fix. A push-delete/mass-delete in a NON-FIRST
        compound leg is ungated (first-leg anchoring). If this assertion FAILS, the
        recognition was hardened into a match-anywhere scan that over-blocks faithful
        clicks — that is the WRONG fix; re-read the conservative-recognition principle."""
        assert D(cmd) is False, (
            f"Recognition was hardened to chase a non-first-leg push-delete — this "
            f"RE-INTRODUCES a faithful-click over-block. Do NOT 'fix' this form: {cmd!r}"
        )

    def test_httpie_protection_ungated_by_design(self):
        """ACCEPTED LIMITATION — do NOT fix by bolting on an httpie read arm. The mint
        classifier covers gh-api/curl/wget only; an httpie protection READ arm would
        gate a form the mint cannot bind = a gated-but-unmintable over-block (itself a
        faithful-click block). Leaving it ungated keeps read == mint. Full httpie
        gate+mint is a deferred scope expansion, not a half-measure read arm."""
        assert D("http DELETE https://api.github.com/repos/o/r/branches/main/protection") is False

    @pytest.mark.parametrize(
        "cmd,expected_op",
        [
            ("cd /repo && git push origin main --force", "force-push"),
            ("cd /repo && git branch -D feature", "branch-delete"),
            ("cd /repo && gh pr merge 5", "merge"),
            ("cd /repo && gh api -X DELETE repos/o/r/branches/main/protection", "branch-protection"),
        ],
    )
    def test_literal_arm_contrast_still_gates_non_first_leg(self, cmd, expected_op):
        """THE BOUND on the accepted-ungated surface (and the non-vacuity for the pins
        above): the LITERAL DANGEROUS_PATTERNS arms (force-push, branch -D, gh pr
        merge, the API ref/protection arms) match-anywhere and STILL gate in a
        NON-FIRST leg. This proves the accepted-ungated set is SPECIFIC to the
        parse-dependent union-arm push forms — NOT a general compound bypass — so the
        accepted set cannot silently widen. If a literal arm ever stops gating in a
        non-first leg, that is a real under-block and this turns red."""
        assert D(cmd) is True, f"UNDER-BLOCK: literal arm stopped gating in a non-first leg: {cmd!r}"
        assert OP(cmd) == expected_op


class TestParityCanaryReconciledForWidenedForms:
    """§9.A parity reconciliation (review-cycle-1). After the mint-widening, the mint
    classifier AGREES with the read floor for the lowercase / mixed-case / global-flag
    forms, so the mint==read EQUALITY now holds for them (pre-fix these were
    read=True / detect=None — a gated-but-unmintable over-block). This extends the
    §9.A canary beyond the uppercase-only forms the original suite covered."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api -X delete repos/o/r/git/refs/heads/y",                 # lowercase
            "gh api --method delete repos/o/r/git/refs/heads/y",          # lowercase --method
            "gh api -X Delete repos/o/r/branches/main/protection",        # mixed-case
            "gh -R o/r api -X DELETE repos/o/r/branches/main/protection", # global-flag framing
            "curl -X put https://git.example.com/repos/o/r/branches/main/protection",  # curl lowercase
        ],
    )
    def test_mint_equals_read_parity_now_holds(self, cmd):
        assert mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)
