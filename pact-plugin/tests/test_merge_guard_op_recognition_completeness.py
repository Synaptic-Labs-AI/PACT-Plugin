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
from merge_guard_pre import check_merge_authorization  # noqa: E402
from merge_guard_post import (  # noqa: E402
    _mint_context_from_bundle,
    _target_value,
    write_token,
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


class TestCrossLegFlagLeakOverBlockGone:
    """#1078: the flag-condition union arm derived FLAGS from the WHOLE command
    (`_shell_tokenize(command)`) while POSITIONALS came from the first executable
    leg — so a force/delete flag in a benign CONTINUATION leg leaked in and
    mislabeled a benign first-leg op (`git push origin feature && rm -rf build/`
    gated as force-push; `git push && rm -rf build/` was PERMANENTLY blocked:
    gated with no extractable target → unmintable). The fix anchors the ENTIRE
    arm — tokens, coarse-shape predicates, extractor inputs — to
    `_executable_prefix`, curing the close and branch-delete sibling arms at the
    same shared site. Mirrors TestOverBlockGoneRegression's cross-leg-gone block;
    the counter-mutation below proves these assertions are coupled to THIS fix."""

    # --- the 5 cured forms (+ separator variants of the primary) ---

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push origin feature && rm -rf build/",   # was mislabeled force-push
            "git push origin feature ; rm -rf build/",    # ; variant
            "git push origin feature | rm -rf build/",    # | variant
            "git push && rm -rf build/",                  # was PERMANENTLY blocked (no target)
            "git push origin && rm -rf build/",           # was PERMANENTLY blocked
            "gh pr close 42 && git branch -d temp",       # close sibling: leaked -d
            "git branch --delete temp && git checkout --force main",  # branch-delete sibling: leaked --force
        ],
    )
    def test_cross_leg_flag_leak_gone(self, cmd):
        assert D(cmd) is False, f"OVER-BLOCK regressed: cross-leg flag leak re-appeared: {cmd!r}"

    # --- non-vacuity contrast: single-first-leg flag forms still gate, correct op ---

    @pytest.mark.parametrize(
        "cmd,expected_op",
        [
            ("git push --force origin main", "force-push"),
            ("git branch -Df temp", "branch-delete"),
            ("gh pr close 5 -d", "close"),
        ],
    )
    def test_single_first_leg_flag_form_still_gates_control(self, cmd, expected_op):
        """Non-vacuity control: the same danger flags IN the first leg still gate
        with the correct op-class, so the D=False assertions above discriminate
        first-leg-anchoring from a blanket 'compound is always safe'."""
        assert D(cmd) is True
        assert OP(cmd) == expected_op

    # --- parity: no gated-but-unmintable state for any cured form ---

    @pytest.mark.parametrize(
        "cmd,expected_op",
        [
            # union fallback abstains -> detect None; the equality holds (False==False)
            ("git push origin feature && rm -rf build/", None),
            ("git push && rm -rf build/", None),
            ("git push origin && rm -rf build/", None),
            ("git branch --delete temp && git checkout --force main", None),
            # close sibling: detect stays "close" (see docstring), still ungated
            ("gh pr close 42 && git branch -d temp", "close"),
        ],
    )
    def test_no_gated_but_unmintable_state_for_cured_forms(self, cmd, expected_op):
        """No cured form can be gated-but-unmintable (that state needs D=True +
        detect=None; every row here is D=False). For the push/branch rows the
        stronger mint==read EQUALITY also holds (both sides reach the same fixed
        union arm and abstain). The close row differs BY DELIBERATE PRE-EXISTING
        DESIGN: detect's literal close arm classifies ANY `gh pr close` variant
        ('"close" - gh pr close (any variant)' per its own docstring — the close
        class is folded for symmetric authorization), so OP stays "close" while
        the read floor abstains — mint-wider-than-read, the SAFE direction (an
        ungated command never consults a token). The "close" expectation below
        DOCUMENTS that pre-existing behavior; it is NOT a contract that the close
        row must never become None — if a future change makes bare-close detect
        None, that is a separate design question, not a regression this test
        should veto."""
        assert D(cmd) is False
        assert OP(cmd) == expected_op

    # --- literal force-push arms are leg-isolated too (#1082 — the former
    #     residual pins FLIPPED with the fix, per their own docstring contract) ---

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push origin feature && rm -f stale.txt",
            "git push origin feature && rm --force stale.txt",
            "git push && rm -f x.txt",   # was the PERMANENT block (no target -> unmintable)
            "git push origin feature ; rm -f stale.txt",   # ; variant
            "git push origin feature | rm -f stale.txt",   # | variant
        ],
    )
    def test_literal_arm_cross_leg_span_cured(self, cmd):
        """#1082 CURED: the literal force-push arms (_FORCE_PUSH_LITERAL_ARMS, one
        SSOT feeding read floor AND detect) now match PER-LEG over the shared
        _slice_stripped_legs substrate, so a benign push chained with `rm -f`/
        `rm --force` no longer gates — including the formerly PERMANENT no-target
        member. Parity: detect abstains identically (per-leg force miss; the
        first-leg-anchored union arm returns None), so no gated-but-unmintable
        state can arise."""
        assert D(cmd) is False, f"literal-arm cross-leg over-block regressed: {cmd!r}"
        assert OP(cmd) is None

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push --force origin main",                         # single leg
            "cd /repo && git push --force origin main",             # flag+push together in a NON-FIRST leg
            "git push --force origin main && echo ok",              # first-leg force + benign continuation
            "git push --force origin main 2>&1",                    # FD redirect — one leg (FD-neutralized)
            'git push --push-option "a && b" --force origin main',  # QUOTED separator — one leg
            'bash -c "git push --force origin main"',               # quoted single leg
            "GIT_TRACE=1 git push --force origin main",             # env-prefix
            "git push --force \\\norigin main",                     # line continuation
        ],
    )
    def test_literal_arm_same_leg_still_gates(self, cmd):
        """The no-new-under-block set: push + force-class flag co-occurring within
        ONE leg still gates in ANY leg position (the literal floor keeps its
        match-anywhere purpose, per leg). Includes the quoted-separator row — a
        `&&` inside quotes is not a leg boundary — which a tempered-regex span
        (`[^&|;]*`) would have wrongly ungated; that is why the fix is per-leg
        matching over the substrate, not a regex rewrite."""
        assert D(cmd) is True, f"NEW UNDER-BLOCK: same-leg force-push stopped gating: {cmd!r}"

    def test_literal_arm_same_leg_control_detects_force_push(self):
        """Non-vacuity control + mint parity for the preserved set: the
        non-first-leg same-leg form classifies force-push on the mint side too."""
        assert OP("cd /repo && git push --force origin main") == "force-push"

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push origin feature # then rm -f stale.txt",   # comment strip
            "git push origin feature && echo 'rm -f x'",        # echo-argument strip
            "git push origin feature <<EOF\nrm -f x && git push --force origin main\nEOF",  # heredoc body strip
        ],
    )
    def test_literal_arm_strip_pipeline_non_divergence(self, cmd):
        """Per-leg matching runs on the SAME stripped substrate the whole-string
        matching used, so every strip (comment / echo-argument / heredoc) acts
        identically on both — these rows were ungated before the leg isolation
        and stay ungated after it. The only behavioral delta of the fix is the
        leg PARTITION of the same stripped string."""
        assert D(cmd) is False

    def test_literal_arm_leg_isolation_is_non_vacuous_under_whole_string_mutation(self, monkeypatch):
        """Both-direction counter-mutation for the literal-arm leg isolation:
        monkeypatch `_slice_stripped_legs` → single-whole-leg (the pre-fix
        whole-string surface; the module-global binding both is_dangerous_command
        and _split_into_legs resolve at call time) and assert the formerly
        PERMANENT member flips back to dangerous — proving the cured rows above
        are coupled to the leg partition, not vacuously green. In-memory
        (monkeypatch) by design — no git-checkout mutation in the shared
        worktree."""
        cmd = "git push && rm -f x.txt"
        # direction 1 — fix present: benign compound runs free
        assert mgc.is_dangerous_command(cmd) is False
        # direction 2 — pre-fix whole-string surface restored: the over-block returns
        monkeypatch.setattr(mgc, "_slice_stripped_legs", lambda s: [s])
        assert mgc.is_dangerous_command(cmd) is True, (
            "whole-leg mutation did not restore the pre-fix literal-arm over-block "
            "— the cured-row assertions would be vacuous"
        )

    # --- non-vacuity: in-memory counter-mutation, executed in both directions ---

    def test_first_leg_anchoring_is_non_vacuous_under_whole_command_mutation(self, monkeypatch):
        """Simulate the PRE-FIX whole-command feed by monkeypatching
        `_executable_prefix` → identity (the module-global binding
        `_flag_condition_danger_op` resolves at call time), and assert the primary
        cured form flips BACK to dangerous — proving the D=False assertions above
        are coupled to the first-leg anchoring, not vacuously green. Identity is a
        faithful pre-fix simulation because the fix routes the entire arm through
        that one feed. In-memory (monkeypatch) by design — a git-revert mutation
        would clobber peers' uncommitted work in the shared worktree."""
        cmd = "git push origin feature && rm -rf build/"
        # direction 1 — fix present: benign compound runs free
        assert mgc.is_dangerous_command(cmd) is False
        # direction 2 — pre-fix feed restored: the cross-leg leak returns
        monkeypatch.setattr(mgc, "_executable_prefix", lambda c: c)
        assert mgc.is_dangerous_command(cmd) is True, (
            "identity-prefix mutation did not restore the pre-fix over-block — "
            "the cured-form assertions would be vacuous"
        )


class TestCloseLiteralArmCrossLegSweep:
    """#1087 CLOSE cross-leg completion — the permanent bidirectional sweep for
    the per-leg `_CLOSE_LITERAL_ARMS` conversion, modeled on the #1082 force-push
    pair (`test_literal_arm_cross_leg_span_cured` / `_same_leg_still_gates`).

    The close danger arms (`gh pr close` + `--delete-branch`) previously ran their
    `.*`/lookahead over the WHOLE stripped command, so a `--delete-branch` token in
    a benign continuation leg fired the arm cross-leg — an OVER-BLOCK, and the
    substrate of the #1087 laundering (the ambiguous multi-close minted a close
    token that authorized an escalated same-target single). The conversion matches
    per-leg: an arm fires iff `gh pr close` and `--delete-branch` co-occur within
    ONE leg. Per §0, the over-block-REMOVED direction is the PRIMARY/INVIOLABLE
    gate proven first; the same-leg-STILL-gates direction is the secondary
    no-new-under-block sweep."""

    # --- PRIMARY (§0-inviolable): over-block REMOVED — benign compounds run FREE ---
    @pytest.mark.parametrize(
        "cmd",
        [
            "gh pr close 42 && gh pr close 43 && echo --delete-branch",  # AMBIG (the #1087 attack)
            "gh pr close 42 && echo --delete-branch",                    # single-member
            "echo --delete-branch && gh pr close 42",                    # reversed
        ],
    )
    def test_close_arm_cross_leg_span_cured(self, cmd):
        """#1087 CURED: a bare `gh pr close` chained with a `--delete-branch` token
        in a SEPARATE leg no longer gates (the token and the close verb never
        co-occur in one leg). This is the over-block removal AND the laundering
        substrate removal — the ambiguous form is now is_dangerous=False, so the
        mint write-gate refuses and no token can be minted to launder."""
        assert D(cmd) is False, f"CLOSE cross-leg over-block regressed: {cmd!r}"

    # --- SECONDARY (no-new-under-block): same-leg close+flag STILL gates ---
    @pytest.mark.parametrize(
        "cmd",
        [
            "gh pr close 42 --delete-branch",                              # single leg
            "cd /repo && gh pr close 42 --delete-branch",                  # CRITICAL must-not-regress (co-occur in leg[1])
            'git commit -m "a && b" && gh pr close 42 --delete-branch',    # quoted && is substrate, real close leg gates
            "bash -c 'gh pr close 42 --delete-branch'",                    # quoted single leg
            "gh --repo o/r pr close 42 --delete-branch",                   # global-flag prefix
            "gh pr close 42 -d",                                           # first-leg flag-condition union arm
            "gh pr close -d 42",                                           # flag before positional
            "gh pr close 42 -cd",                                          # clustered short flag
            "gh pr close 5 -d && echo done",                              # first-leg -d + benign continuation
        ],
    )
    def test_close_arm_same_leg_still_gates(self, cmd):
        """The no-new-under-block set: `gh pr close` + delete flag co-occurring
        within ONE leg still gates in ANY leg position — including the CRITICAL
        `cd /repo && gh pr close 42 --delete-branch` (True today ONLY via the
        in-leg per-leg match the #1087 conversion re-establishes; it was True
        pre-fix via the cross-leg lookahead the fix removes). The `-d`/`-cd`
        short-flag forms gate via the first-leg flag-condition union arm."""
        assert D(cmd) is True, f"NEW UNDER-BLOCK: same-leg dangerous close stopped gating: {cmd!r}"

    def test_close_arm_same_leg_control_classifies_close(self):
        """Non-vacuity control for the same-leg set: the preserved forms classify
        as `close`, so the D=True rows discriminate a real gated close from a
        blanket 'compound is always safe'."""
        assert OP("cd /repo && gh pr close 42 --delete-branch") == "close"
        assert OP("gh pr close 42 --delete-branch") == "close"

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh pr close 42 && gh pr close 43 && echo --delete-branch",
            "gh pr close 42 && echo --delete-branch",
            "echo --delete-branch && gh pr close 42",
        ],
    )
    def test_close_cured_rows_non_vacuous_and_single_family(self, cmd, monkeypatch):
        """Row-by-row non-vacuity AND identity-slice faithfulness for every cured
        close form. Two-stage counter-mutation over the module-global bindings
        `is_dangerous_command` resolves at call time:

          direction 1 (fix present): the benign compound runs free (D is False).
          direction 2 (`_slice_stripped_legs` -> identity, the pre-fix whole-command
            surface): the over-block RETURNS (D is True) — coupling the assertion
            to the per-leg partition.
          faithfulness (identity-slice + `_CLOSE_LITERAL_ARMS` neutered): D returns
            to False — proving the whole-command flip is caused by the CLOSE family
            arm ALONE, with no second-family (force-push/API/flag-condition)
            co-match. This is why the identity-slice mutation is a FAITHFUL pre-fix
            simulation for this row (the coder flagged this for reviewer
            confirmation; it is confirmed here per-row, not assumed single-family)."""
        assert mgc.is_dangerous_command(cmd) is False
        monkeypatch.setattr(mgc, "_slice_stripped_legs", lambda s: [s])
        assert mgc.is_dangerous_command(cmd) is True, (
            f"whole-command mutation did not restore the pre-fix close over-block "
            f"— the cured-row assertion would be vacuous: {cmd!r}"
        )
        monkeypatch.setattr(mgc, "_CLOSE_LITERAL_ARMS", ())
        assert mgc.is_dangerous_command(cmd) is False, (
            f"whole-command flip survived close-arm neutering — a SECOND family "
            f"co-matches, so the identity-slice mutation is NOT a faithful "
            f"single-family pre-fix simulation for this row: {cmd!r}"
        )


class TestApiLiteralArmCrossLegSweep:
    """#1086 API cross-leg completion — the permanent bidirectional sweep for the
    per-leg `_API_LITERAL_ARMS` conversion (17 arms), modeled on the #1082
    force-push pair. The API danger arms previously ran their `.*` over the WHOLE
    stripped command, so a mutating method / body-flag token in a benign
    continuation leg (`gh api .../git/refs && echo -X DELETE`) over-blocked the
    benign compound. Per-leg now: an arm fires iff the API client, the mutating
    method (or implicit-POST body flag), and the target endpoint co-occur within
    ONE leg. Unlike close, the API emergent compounds are PURE over-blocks (an
    isolated bare API leg is method-less hence detect-negative, so tier-2 abstains
    symmetrically — no laundering asymmetry; pinned by the OPEN-Q D tripwire in
    test_merge_guard_over_block_batch.py). §0 priority: over-block-removed first."""

    # --- PRIMARY (§0-inviolable): over-block REMOVED — one row per family ---
    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api repos/o/r/git/refs/heads/x && echo -X DELETE",          # git/refs DELETE
            "gh api repos/o/r/git/refs/heads/x && echo -X PATCH",           # git/refs mutate
            "gh api repos/o/r/branches/main/protection && echo -X DELETE",  # protection
            "gh api repos/o/r/pulls/5/merge && echo -X PUT",                # merge
            "gh api repos/o/r/contents/f && echo -X PUT main",              # contents
            "gh api repos/o/r/git/refs/heads/x && echo -f sha=abc",         # implicit-POST body flag
            "curl https://api.github.com/repos/o/r/git/refs/heads/x && echo -X DELETE",     # curl git/refs
            "wget https://api.github.com/repos/o/r/git/refs/heads/x && echo --method=DELETE",  # wget git/refs
        ],
    )
    def test_api_arm_cross_leg_span_cured(self, cmd):
        """#1086 CURED: an API read in one leg with a mutating method / body-flag
        token in a SEPARATE (benign `echo`) leg no longer gates — the method and
        endpoint never co-occur in one leg. One representative row per API danger
        family (git/refs DELETE + mutate, protection, merge, contents,
        implicit-POST, curl, wget)."""
        assert D(cmd) is False, f"API cross-leg over-block regressed: {cmd!r}"

    # --- SECONDARY (no-new-under-block): same-leg dangerous API STILL gates ---
    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api -X DELETE repos/o/r/git/refs/heads/x",                 # single leg
            "cd /repo && gh api -X DELETE repos/o/r/git/refs/heads/x",     # non-first leg (method+path co-occur)
            "gh api repos/o/r/pulls/5/merge -X PUT",                       # merge, method after path
            "curl -X DELETE https://api.github.com/repos/o/r/git/refs/heads/x",  # curl same-leg
            "wget --method=DELETE https://api.github.com/repos/o/r/git/refs/heads/x",  # wget same-leg
            "gh api -X DELETE repos/o/r/branches/main/protection",        # protection (first-leg)
            "gh api -f sha=abc repos/o/r/git/refs/heads/x",               # implicit-POST body flag same-leg
        ],
    )
    def test_api_arm_same_leg_still_gates(self, cmd):
        """The no-new-under-block set: the API client + mutating method (or
        implicit-POST body flag) + endpoint co-occurring within ONE leg still gates
        in ANY leg position. (The non-first-leg protection form
        `cd /repo && gh api -X DELETE .../branches/main/protection` is separately
        pinned at `test_literal_arm_contrast_still_gates_non_first_leg`; the
        git/refs non-first-leg row here is its sibling, not a duplicate.)"""
        assert D(cmd) is True, f"NEW UNDER-BLOCK: same-leg dangerous API stopped gating: {cmd!r}"

    def test_api_arm_same_leg_control_detects_op(self):
        """Non-vacuity control: the preserved same-leg API forms classify to a
        real destructive op (detect untouched by the read-floor conversion), so
        the D=True rows discriminate a real gated API call."""
        assert OP("gh api -X DELETE repos/o/r/git/refs/heads/x") == "branch-delete"
        assert OP("gh api -X DELETE repos/o/r/branches/main/protection") == "branch-protection"

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api repos/o/r/git/refs/heads/x && echo -X DELETE",
            "gh api repos/o/r/git/refs/heads/x && echo -X PATCH",
            "gh api repos/o/r/branches/main/protection && echo -X DELETE",
            "gh api repos/o/r/pulls/5/merge && echo -X PUT",
            "gh api repos/o/r/contents/f && echo -X PUT main",
            "gh api repos/o/r/git/refs/heads/x && echo -f sha=abc",
            "curl https://api.github.com/repos/o/r/git/refs/heads/x && echo -X DELETE",
            "wget https://api.github.com/repos/o/r/git/refs/heads/x && echo --method=DELETE",
        ],
    )
    def test_api_cured_rows_non_vacuous_and_single_family(self, cmd, monkeypatch):
        """Row-by-row non-vacuity AND identity-slice faithfulness for every cured
        API form (same two-stage counter-mutation as the close sweep):

          direction 1 (fix present): benign compound runs free (D is False).
          direction 2 (`_slice_stripped_legs` -> identity): the over-block RETURNS
            (D is True) — coupling the assertion to the per-leg partition.
          faithfulness (identity-slice + `_API_LITERAL_ARMS` neutered): D returns to
            False — proving the whole-command flip is caused by the API family arm
            ALONE (no force-push/close/flag-condition co-match), so the
            identity-slice mutation is a faithful single-family pre-fix simulation
            for this row."""
        assert mgc.is_dangerous_command(cmd) is False
        monkeypatch.setattr(mgc, "_slice_stripped_legs", lambda s: [s])
        assert mgc.is_dangerous_command(cmd) is True, (
            f"whole-command mutation did not restore the pre-fix API over-block "
            f"— the cured-row assertion would be vacuous: {cmd!r}"
        )
        monkeypatch.setattr(mgc, "_API_LITERAL_ARMS", ())
        assert mgc.is_dangerous_command(cmd) is False, (
            f"whole-command flip survived API-arm neutering — a SECOND family "
            f"co-matches, so the identity-slice mutation is NOT a faithful "
            f"single-family pre-fix simulation for this row: {cmd!r}"
        )


class _ApiMergeDetectArmDisabledRe:
    """A drop-in for merge_guard_common's `re` module that forces
    re.search(r'pulls/\\d+/merge\\b', ...) to return None — surgically disabling
    ONLY the API-merge detect arm's endpoint condition (the per-leg
    `re.search(r"pulls/\\d+/merge\\b", _leg)` inside detect_command_operation_type)
    so the pre-fix detect surface (API merge unclassified) can be measured through
    the REAL machinery. `_extract_api_merge_pr` uses the DIFFERENT literal
    `pulls/(\\d+)/merge\\b` (capturing group) and is unaffected; every compiled
    DANGEROUS_PATTERNS object calls its own .search, not mgc.re. Mirrors the
    _ContentsGuardDisabledRe seam above."""

    def search(self, pattern, string, *args, **kwargs):  # noqa: A003
        if pattern == r"pulls/\d+/merge\b":
            return None
        return _real_re.search(pattern, string, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(_real_re, name)


class TestApiMergeMintParity:
    """#1096 API-merge mint parity — the bidirectional cert for the additive
    per-leg detect arm (gh api|curl|wget + mutating PUT/PATCH/POST + a
    pulls/<N>/merge endpoint in ONE leg → "merge") plus the path-based
    `_extract_api_merge_pr` wired into `extract_command_context` as the
    pr_number fallback.

    Pre-fix, a mutating API PR-merge was the gated-but-unmintable class: the
    read floor gated it (DANGEROUS_PATTERNS API-merge arms) but detect returned
    None, so the mint write-gate refused → a faithful API-merge click could
    NEVER mint — a PERMANENT over-block. The cure raises recognition in the ONE
    detect SSOT (mint + read + retirement observer all served from one site;
    never a mint-only recognition fork).

    Priority per the governing principle: over-block-CURED (mints + authorizes)
    is the PRIMARY/inviolable direction; additive purity is co-inviolable (an
    existing classification change could re-block a faithful click elsewhere);
    the no-new-under-block rows are the secondary sweep. curl/wget approvals are
    driven via the QUOTED (backtick) option form — the canonical mint path; a
    BARE unquoted curl/wget does not mint (locate_command_regions substrate,
    pre-existing all-API-arm residual, NOT a #1096 regression)."""

    def _authorize(self, cmd, tmp_path):
        """Mint `cmd` via the real bundle path, write the token, and run the
        real read-side authorization. Returns (ctx, verdict)."""
        ctx, refusal = mint(cmd)
        if ctx is None:
            return None, f"NO-MINT({refusal})"
        write_token(ctx, token_dir=tmp_path)
        err = check_merge_authorization(cmd, token_dir=tmp_path)
        return ctx, ("ALLOW" if err is None else "DENY")

    # --- PRIMARY (inviolable): every faithful spelling classifies + mints ---

    @pytest.mark.parametrize(
        "cmd,expected_flags",
        [
            ("gh api -X PUT /repos/o/r/pulls/42/merge", []),
            ("gh api -X PATCH /repos/o/r/pulls/42/merge", []),
            ("gh api -X POST /repos/o/r/pulls/42/merge", []),
            ("curl -X PUT https://api.github.com/repos/o/r/pulls/42/merge", []),
            ("wget --method=PUT https://api.github.com/repos/o/r/pulls/42/merge", []),
            ("gh -R o/r api /repos/o/r/pulls/42/merge -X PUT", ["--repo=o/r"]),
        ],
    )
    def test_api_merge_spelling_classifies_and_mints(self, cmd, expected_flags):
        """#1096 CURED: every faithful API-merge spelling detects as `merge` and
        mints a pr-targeted token. The gh-global-flag row is the Ruling-B cure
        (tolerant _GH_API_PREFIX client match) and binds its REAL -R flag as
        [--repo=o/r] (ruled correct: a real denylist flag, spelling-appropriate);
        every path-resident-only spelling binds []."""
        assert OP(cmd) == "merge", f"API-merge spelling unclassified: {cmd!r}"
        ctx, refusal = mint(cmd)
        assert ctx is not None, f"OVER-BLOCK regressed (no mint): {cmd!r} ({refusal})"
        assert ctx["pr_number"] == "42"
        assert ctx["bound_flags"] == expected_flags

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api -X PUT /repos/o/r/pulls/42/merge",
            "curl -X PUT https://api.github.com/repos/o/r/pulls/42/merge",
            "wget --method=PUT https://api.github.com/repos/o/r/pulls/42/merge",
            "gh -R o/r api /repos/o/r/pulls/42/merge -X PUT",
        ],
    )
    def test_api_merge_faithful_round_trip_authorizes(self, cmd, tmp_path):
        """The full faithful click: approve → mint → byte-identical re-execution
        AUTHORIZES through the real read path (the §0 inviolable round-trip)."""
        _ctx, verdict = self._authorize(cmd, tmp_path)
        assert verdict == "ALLOW", (
            f"faithful API-merge round-trip blocked ({verdict}): {cmd!r}"
        )

    def test_global_flag_repo_bind_refuses_cross_repo(self, tmp_path):
        """The [--repo] round-trip's safe edge: the repo-scoped token REFUSES an
        exec against a DIFFERENT repo (the escalation-discriminator direction)."""
        ctx, refusal = mint("gh -R o/r api /repos/o/r/pulls/42/merge -X PUT")
        assert ctx is not None and ctx["bound_flags"] == ["--repo=o/r"]
        write_token(ctx, token_dir=tmp_path)
        err = check_merge_authorization(
            "gh -R other/x api /repos/other/x/pulls/42/merge -X PUT",
            token_dir=tmp_path)
        assert err is not None, "cross-repo exec must REFUSE against a repo-bound token"

    # --- cross-spelling authorization (correct same-op + non-laundering) ---

    def test_cli_approval_authorizes_api_exec_same_flags(self, tmp_path):
        """Cross-spelling is CORRECT same-operation authorization: a plain CLI
        approval (bound []) authorizes the plain API exec of the SAME pr (both
        derive {merge, 42, []} from the one extract_command_context)."""
        ctx, _ = mint("gh pr merge 42")
        assert ctx is not None and ctx["bound_flags"] == []
        write_token(ctx, token_dir=tmp_path)
        assert check_merge_authorization(
            "gh api -X PUT /repos/o/r/pulls/42/merge", token_dir=tmp_path) is None

    def test_api_approval_authorizes_cli_exec_same_flags(self, tmp_path):
        ctx, _ = mint("gh api -X PUT /repos/o/r/pulls/42/merge")
        assert ctx is not None and ctx["bound_flags"] == []
        write_token(ctx, token_dir=tmp_path)
        assert check_merge_authorization("gh pr merge 42", token_dir=tmp_path) is None

    def test_privileged_cli_approval_refuses_plain_api_exec(self, tmp_path):
        """Non-laundering: a PRIVILEGED CLI approval (bound [--admin]) does NOT
        authorize the plain API exec (flag-set mismatch) — the privileged
        approval cannot be laundered into a differently-shaped execution."""
        ctx, _ = mint("gh pr merge 42 --admin")
        assert ctx is not None and ctx["bound_flags"] == ["--admin"]
        write_token(ctx, token_dir=tmp_path)
        assert check_merge_authorization(
            "gh api -X PUT /repos/o/r/pulls/42/merge", token_dir=tmp_path) is not None

    def test_api_approval_refuses_different_target(self, tmp_path):
        ctx, _ = mint("gh api -X PUT /repos/o/r/pulls/42/merge")
        assert ctx is not None
        write_token(ctx, token_dir=tmp_path)
        assert check_merge_authorization(
            "gh api -X PUT /repos/o/r/pulls/43/merge", token_dir=tmp_path) is not None

    # --- additive purity + no-new-under-block: negatives stay unmintable ---

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api /repos/o/r/pulls/42/merge",              # bare GET (merge-status read)
            "gh api -X GET /repos/o/r/pulls/42/merge",       # explicit GET
            "gh api -X DELETE /repos/o/r/pulls/42/merge",    # DELETE excluded
            "gh api -X PUT /repos/o/r/merges/xyz",           # non-pulls path (read-gated residual, unmintable)
            "gh api -f commit_title=t /repos/o/r/pulls/42/merge",  # implicit-POST (deliberate residual)
            "gh api repos/o/r/pulls/5/merge && echo -X PUT",       # per-leg canary
            "gh -R o/r api repos/o/r/pulls/5/merge && echo -X PUT",  # per-leg canary, tolerant matcher
        ],
    )
    def test_non_faithful_api_merge_stays_unclassified_and_unmintable(self, cmd):
        """No-new-under-block + additive purity: every non-faithful form stays
        detect-None and unmintable. The per-leg canaries are the rows that would
        FAIL under a whole-command arm (a method keyword in a benign continuation
        leg must not classify the compound) — they hold under the tolerant client
        matcher too (Ruling-B widens only the in-leg client span, not the leg
        isolation). The non-pulls and implicit-POST rows are read-floor-gated
        (D=True) but deliberately unmintable — PRE-EXISTING residuals (read-floor
        breadth / implicit-POST parity with git/refs), not #1096 regressions."""
        assert OP(cmd) is None, f"non-faithful API-merge form classified: {cmd!r}"
        ctx, _refusal = mint(cmd)
        assert ctx is None, f"non-faithful API-merge form MINTED: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd,expected_op",
        [
            ("gh api -X DELETE repos/o/r/git/refs/heads/x", "branch-delete"),
            ("gh api -X PATCH repos/o/r/git/refs/heads/x", "force-push"),
            ("gh api -X DELETE repos/o/r/branches/main/protection", "branch-protection"),
            ("gh pr merge 42", "merge"),
            ("gh pr close 42 --delete-branch", "close"),
            ("git push --force origin main", "force-push"),
            ("git branch -D victim", "branch-delete"),
            ("gh api /repos/o/r/git/refs/heads/x", None),
            ("gh api /repos/o/r/contents/file.txt", None),
            ("echo PUT pulls and merge", None),
        ],
    )
    def test_additive_purity_existing_classifications_unchanged(self, cmd, expected_op):
        """Additive purity (inviolable): the pre-existing detect corpus is
        untouched by the new arm — every existing op-class still classifies
        identically and every previously-None input stays None. The ONLY input
        class the arm changes is the formerly-None mutating pulls/<N>/merge."""
        assert OP(cmd) == expected_op

    # --- non-vacuity: two independent in-memory couplings ---

    def test_mint_rows_non_vacuous_under_detect_arm_disable(self, tmp_path):
        """Direction-2 counter-mutation #1: surgically disable ONLY the API-merge
        detect arm (its exact endpoint literal, via the delegating re-module
        shim) — the pre-fix surface returns: detect None → the mint write-gate
        refuses → the faithful API click is blocked again. The CLI-merge control
        still mints, so the flip is attributable to the NEW arm alone."""
        cmd = "gh api -X PUT /repos/o/r/pulls/42/merge"
        # direction 1 — fix present
        assert OP(cmd) == "merge"
        ctx, _ = mint(cmd)
        assert ctx is not None
        # direction 2 — arm disabled: pre-fix gated-but-unmintable returns
        saved = mgc.re
        mgc.re = _ApiMergeDetectArmDisabledRe()
        try:
            assert OP(cmd) is None, (
                "arm-disable shim did not restore the pre-fix detect surface — "
                "the cured-spelling assertions would be vacuous"
            )
            ctx2, _ = mint(cmd)
            assert ctx2 is None, "mint survived the detect-arm disable"
            ctl, _ = mint("gh pr merge 42")
            assert ctl is not None, "CLI-merge control must be unaffected by the shim"
        finally:
            mgc.re = saved

    def test_mint_rows_non_vacuous_under_extractor_neuter(self, monkeypatch):
        """Direction-2 counter-mutation #2: neuter `_extract_api_merge_pr` to
        always-None — detect still says merge, but the context loses its
        pr_number, `_collect_pairs` admits no (op, target) pair, and the mint
        REFUSES — proving the mint rows are coupled to the extractor wiring
        (recognition<->extractability), not just the detect arm."""
        cmd = "gh api -X PUT /repos/o/r/pulls/42/merge"
        ctx, _ = mint(cmd)
        assert ctx is not None
        monkeypatch.setattr(mgc, "_extract_api_merge_pr", lambda c: None)
        assert OP(cmd) == "merge"  # detect unaffected — isolates the extractor
        ctx2, _ = mint(cmd)
        assert ctx2 is None, (
            "extractor neuter did not break the mint — the pr-target wiring "
            "assertions would be vacuous"
        )
        ctl, _ = mint("gh pr merge 42")
        assert ctl is not None, "CLI-merge control must be unaffected"


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
        gate+mint was DECLINED (maintainer ruled httpie out-of-charter), not a
        half-measure read arm."""
        assert D("http DELETE https://api.github.com/repos/o/r/branches/main/protection") is False

    def test_httpie_git_refs_ungated_by_design(self):
        """ACCEPTED LIMITATION (#1077) — sibling of the protection pin above. The
        httpie ref-mutation read arms were REMOVED (they gated forms the
        gh-api/curl/wget-only mint could not bind = a PERMANENT gated-but-unmintable
        over-block). Ungated BY DESIGN, #1079-consistent; do NOT re-gate."""
        assert D("http DELETE https://api.github.com/repos/o/r/git/refs/heads/feature") is False

    def test_httpie_merge_ungated_by_design(self):
        """ACCEPTED LIMITATION (#1077) — sibling of the protection pin above: httpie
        merge mutation is ungated BY DESIGN (same removed-read-arm rationale as the
        git/refs pin); do NOT re-gate."""
        assert D("http PUT https://api.github.com/repos/o/r/pulls/42/merge") is False

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

    @pytest.mark.parametrize(
        "cmd",
        [
            "cd /repo && git branch -Df temp",   # cluster force-delete in a non-first leg
            "cd /repo && gh pr close 5 -d",      # short -d close in a non-first leg
        ],
    )
    def test_non_first_leg_cluster_flag_forms_ungated_by_design(self, cmd):
        """ACCEPTED LIMITATION — do NOT fix. The flag-condition union arm is
        FIRST-LEG-ANCHORED, so a clustered/short danger flag in a NON-FIRST leg
        loses union coverage. Re-detecting these requires whole-command or per-leg
        flag derivation — exactly the cross-leg flag leak the anchoring removed /
        the pinned over-block reintroduction. The idiomatic spellings (`-D`,
        `--delete-branch`) remain caught match-anywhere by the literal floor (the
        contrast rows in this class)."""
        assert D(cmd) is False, (
            f"Recognition was widened to chase a non-first-leg cluster flag — this "
            f"RE-INTRODUCES a faithful-click over-block. Do NOT 'fix' this form: {cmd!r}"
        )


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
            # Lease-to-default fold (#1064): parity holds POSITIVELY for the
            # lease-to-default spellings (read gates AND mint classifies — the
            # mint arm's lease-excluding lookahead was the gated-but-unmintable
            # over-block this canary hunts) ...
            "git push --force-with-lease origin main",
            "git push --force-with-lease origin master",
            "git push --force-with-lease=main:abc123 origin main",
            # ... and NEGATIVELY for lease-to-feature (ungated AND unminted —
            # the fold must not widen beyond the default-branch arm).
            "git push --force-with-lease origin feature",
        ],
    )
    def test_mint_equals_read_parity_now_holds(self, cmd):
        assert mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)


class TestHttpieMembershipCompleteness:
    """Executable membership-completeness check for the httpie drop (#1077).

    With the two httpie read-floor arms (git/refs + merge) removed, NO executable
    gate site may see httpie: the read floor must not gate (D is False) AND the
    mint classifier must not classify (OP is None) for the full httpie probe set,
    in both alias spellings (`http` / `https`). Both httpie families are thereby
    tolerated under-blocks, #1079-consistent: ref-mutation/merge (newly dropped)
    and branch-protection (never gated).

    If ANY row flips, either a second httpie gate site exists (the two removed
    arms were NOT the complete membership) or httpie was re-gated without mint
    coverage — both re-create the gated-but-unmintable over-block this change
    removed. Do NOT re-gate; httpie is wholly out of charter.
    """

    @pytest.mark.parametrize(
        "cmd",
        [
            # ref-mutation family (the removed git/refs arm's probe surface)
            "http DELETE https://api.github.com/repos/o/r/git/refs/heads/feature",
            "http PATCH https://api.github.com/repos/o/r/git/refs/heads/feature",
            "http POST https://api.github.com/repos/o/r/git/refs",
            "http PUT https://api.github.com/repos/o/r/git/refs/heads/feature",
            # merge family (the removed merge arm's probe surface)
            "http DELETE https://api.github.com/repos/o/r/pulls/42/merge",
            "http PATCH https://api.github.com/repos/o/r/pulls/42/merge",
            "http PUT https://api.github.com/repos/o/r/pulls/42/merge",
            # flag/case variants the removed arms used to catch
            "http -a user:pass DELETE https://api.github.com/repos/o/r/git/refs/heads/feature",
            "http delete https://api.github.com/repos/o/r/git/refs/heads/feature",
            # `https` alias spelling of both families
            "https DELETE https://api.github.com/repos/o/r/git/refs/heads/feature",
            "https PUT https://api.github.com/repos/o/r/pulls/42/merge",
            # branch-protection family (never gated — #1079)
            "http DELETE https://api.github.com/repos/o/r/branches/main/protection",
            "https PUT https://api.github.com/repos/o/r/branches/main/protection",
        ],
    )
    def test_httpie_membership_is_empty_read_and_mint(self, cmd):
        assert D(cmd) is False, (
            f"httpie gate site found on the READ floor — re-creates the "
            f"gated-but-unmintable over-block: {cmd!r}"
        )
        assert OP(cmd) is None, (
            f"httpie gate site found in the MINT classifier — contradicts the "
            f"removal-completeness certificate: {cmd!r}"
        )

    @pytest.mark.parametrize(
        "cmd",
        [
            # discriminating positives: the IDIOMATIC clients stay gated, proving the
            # all-False httpie rows above are a real membership fact, not a broken probe
            "wget --method=DELETE https://api.github.com/repos/o/r/git/refs/heads/feature",
            "curl -X DELETE https://api.github.com/repos/o/r/git/refs/heads/feature",
        ],
    )
    def test_idiomatic_client_contrast_still_gates(self, cmd):
        assert D(cmd) is True, f"idiomatic API client stopped gating: {cmd!r}"
