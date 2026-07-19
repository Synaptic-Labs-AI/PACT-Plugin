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
        value must not be mis-read as a ref-delete refspec.

        OBS-C hardened the force-push/push-to-main target parser to SKIP a
        value-taking push-option's value token (`-o ':oldref'`) via
        `_push_positionals`, recovering the REAL refspec positional (`main`) instead
        of miscounting the colon-bearing value as a 3rd positional and abstaining.
        The prior `target_ref in (None,)` assertion encoded that pre-OBS-C over-block
        miscount; the STRONGER post-OBS-C invariant is: the quoted push-option colon
        is NEVER bound as a delete refspec — the op stays a NON-delete class AND any
        recovered `target_ref` is the real refspec (`main`), never `:oldref`/`:weird`.
        mint==read holds by construction (both derive via extract_command_context)."""
        op = OP(cmd)
        tr = CTX(cmd).get("target_ref")
        # op-axis: the -o-embedded colon must never promote the command to a delete
        # class (the core #1037 protection — kept and widened to mass-delete).
        assert op not in ("remote-ref-delete", "remote-mass-delete"), (
            f"{cmd!r} mis-classified as a delete op: {op!r}"
        )
        # target-axis: the recovered target is the REAL refspec (`main`) or absent,
        # NEVER the quoted push-option colon (the #1037 leak this row guards).
        assert tr in (None, "main"), (
            f"{cmd!r} recovered a non-refspec target_ref: {tr!r}"
        )
        assert tr is None or not tr.startswith(":"), (
            f"{cmd!r} leaked a colon delete-refspec as target_ref: {tr!r}"
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
# destructive IDENTITY (the netstring _canonical_join of the field tuple),
# NOT the whole command.
REMOTE_MASS_DELETE_SPELLINGS = [
    ("git push --mirror origin", mgc._canonical_join(["--mirror", "origin"])),
    ("git push --mirror", mgc._canonical_join(["--mirror", IMPLICIT])),                 # implicit remote
    ("git push --prune origin", mgc._canonical_join(["--prune", "origin"])),
    ("git push --prune origin refs/heads/main", mgc._canonical_join(["--prune", "origin", "refs/heads/main"])),
    ("git push origin --delete a b", mgc._canonical_join(["--delete", "origin", "a", "b"])),       # multi-ref
    ("git push origin :a :b", mgc._canonical_join(["--delete", "origin", "a", "b"])),              # multi colon
    ("git push origin --mirror", mgc._canonical_join(["--mirror", "origin"])),               # flag after remote
    ("git push origin --prune", mgc._canonical_join(["--prune", "origin"])),                 # flag after remote
    ("git push origin --delete a b c", mgc._canonical_join(["--delete", "origin", "a", "b", "c"])),   # three-ref
    ('git push --mirror "origin"', mgc._canonical_join(["--mirror", "origin"])),             # quoted remote
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
        assert CTX(cmd).get("mass_target") == mgc._canonical_join(["--mirror", IMPLICIT])


class TestRemoteMassDeleteCrossAuthDistinctness:
    """§9.H.a — no-cross-auth: distinct destructive identities mint distinct
    tokens, and a token for one mass identity does NOT authorize another (the
    lesser->greater closure that the rejected coarse sentinel would have reopened)."""

    def test_distinct_mass_identities_are_distinct_targets(self):
        assert CTX("git push --prune origin")["mass_target"] != CTX("git push --mirror origin")["mass_target"]
        assert CTX("git push --mirror origin")["mass_target"] != CTX("git push --mirror origin2")["mass_target"]
        assert CTX("git push origin --delete a b")["mass_target"] != CTX("git push origin --delete a c")["mass_target"]

    def test_prune_token_does_not_authorize_mirror(self):
        tok = token("remote-mass-delete", mass_target=mgc._canonical_join(["--prune", "origin"]))
        assert MATCH(tok, "git push --prune origin") is True            # authorizes its own
        assert MATCH(tok, "git push --mirror origin") is False          # not a different identity

    def test_mirror_token_does_not_authorize_prune(self):
        tok = token("remote-mass-delete", mass_target=mgc._canonical_join(["--mirror", "origin"]))
        assert MATCH(tok, "git push --mirror origin") is True
        assert MATCH(tok, "git push --prune origin") is False

    def test_distinct_remote_does_not_cross_authorize(self):
        tok = token("remote-mass-delete", mass_target=mgc._canonical_join(["--mirror", "origin"]))
        assert MATCH(tok, "git push --mirror origin2") is False

    def test_refspec_set_closure_a_b_does_not_authorize_a_c(self):
        tok = token("remote-mass-delete", mass_target=mgc._canonical_join(["--delete", "origin", "a", "b"]))
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
            ("git push origin --delete a b", "remote-mass-delete", mgc._canonical_join(["--delete", "origin", "a", "b"])),
            ("git push --mirror origin", "remote-mass-delete", mgc._canonical_join(["--mirror", "origin"])),
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
            ("git push --mirror origin", mgc._canonical_join(["--mirror", "origin"])),
            ("git push --mirror", mgc._canonical_join(["--mirror", IMPLICIT])),
            ("git push origin --delete a b", mgc._canonical_join(["--delete", "origin", "a", "b"])),
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
        assert MATCH(token("remote-mass-delete", mass_target=mgc._canonical_join(["--mirror", "origin"])), cmd) is True
        assert MATCH(token("remote-mass-delete", mass_target=mgc._canonical_join(["--prune", "origin"])), cmd) is False


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
            ("git push origin --delete a b", mgc._canonical_join(["--delete", "origin", "a", "b"])),
            ("git push --mirror origin", mgc._canonical_join(["--mirror", "origin"])),
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


class TestBranchDeleteLiteralArmCrossLegSweep:
    """#1094 BRANCH-DELETE cross-leg completion — the permanent bidirectional
    sweep for the per-leg `_BRANCH_DELETE_LITERAL_ARMS` conversion, the 4th and
    FINAL cross-leg twin (force-push / close / API precede it), modeled on the
    close/API sweep classes above.

    The three branch-delete danger arms previously ran their `.*` over the WHOLE
    stripped command, so an idiomatic `-D` / `--delete --force` token in a benign
    continuation leg (`git branch new-feature && echo -D`) gated the benign
    compound — and, because detect ALSO classified the compound branch-delete
    whole-command with an extractable target, the ambiguous compound was MINTABLE
    (the close-twin laundering shape; closure canary lives with the close
    canaries in test_merge_guard_auth_symmetry.py). Per-leg now, on BOTH
    consumers of the one SSOT tuple (read floor AND detect): an arm fires iff
    `git branch` and the force-delete flag co-occur within ONE leg.

    The arms are FORWARD-ONLY (`branch` precedes the flag) — unlike close there
    is no reversed arm, so there is no reversed-order cure; the reversed row
    below is a PRE-EXISTING-False pin. The third arm (`--force --delete`,
    whitespace-adjacent) has NO cross-leg over-block to cure (`\\s+` never spans
    a shell operator) — it moved for SSOT symmetry only, so no "cured" row
    exists for it and none should be added. Clustered/split spellings
    (`-Df` / `-fD` / `--delete -f`) are the first-leg-anchored union arm's job,
    untouched in both directions. Per §0, the over-block-REMOVED direction is
    the PRIMARY/INVIOLABLE gate; same-leg-STILL-gates is the secondary
    no-new-under-block sweep."""

    # --- PRIMARY (§0-inviolable): over-block REMOVED — benign compounds run FREE ---
    @pytest.mark.parametrize(
        "cmd",
        [
            "git branch new-feature && echo -D",                 # arm 1, the issue's form
            "git branch --list && echo --delete --force",        # arm 2
            "git branch temp && grep -D skip pattern f.txt",     # arm 1, flag inside a benign leg's args
            "git branch backup ; ls -D",                         # arm 1, `;` separator
            "git checkout main && git branch temp && echo -D",   # arm 1, 3-leg member
            "git branch new-feature || echo -D",                 # arm 1, `||` separator
            "git branch new | grep -D x f.txt",                  # arm 1, `|` separator
            "git branch new & echo -D",                          # arm 1, `&` separator
            "git branch new |& grep -D x f.txt",                 # arm 1, `|&` pipe-both separator
        ],
    )
    def test_branch_delete_arm_cross_leg_span_cured(self, cmd):
        """#1094 CURED, with mint parity in the SAME breath: a benign `git branch`
        leg chained with a stray force-delete token in a SEPARATE leg no longer
        gates (D is False), AND detect abstains identically (OP is None) — the
        gate⟺mint coherence that closes both the mint-scan multiplicity vector
        (a benign compound quoted in AskUserQuestion prose no longer contributes
        a phantom (op,target) pair that could REFUSE a faithful click's mint)
        and the retirement Block-1 drift (an executed benign compound no longer
        classifies branch-delete at the retirement observer)."""
        assert D(cmd) is False, f"BRANCH-DELETE cross-leg over-block regressed: {cmd!r}"
        assert OP(cmd) is None, (
            f"detect still classifies the cured compound — gate/mint coherence "
            f"broken (mint-scan multiplicity vector re-opened): {cmd!r}"
        )

    # --- SECONDARY (no-new-under-block): same-leg branch-delete STILL gates ---
    @pytest.mark.parametrize(
        "cmd",
        [
            "git branch -D temp",                                # arm 1, single leg
            "cd /repo && git branch -D temp",                    # arm 1 in a NON-FIRST leg (union arm abstains — literal arm load-bearing)
            "git branch --delete --force temp",                  # arm 2
            "git branch --force --delete temp",                  # arm 3
            "git branch -D temp && echo done",                   # arm 1 + benign continuation
            'git commit -m "a && b" && git branch -D temp',      # QUOTED separator is NOT a leg boundary
            "bash -c 'git branch -D temp'",                      # quoted single-leg carrier
            "FOO=1 git branch -D temp",                          # env-prefix
            "git branch \\\n-D temp",                            # line continuation (D unchanged; its raw-seam OP is pinned below, not here)
            "git branch -Df temp",                               # union arm, clustered
            "git branch -fD temp",                               # union arm, clustered
            "git branch --delete -f temp",                       # union arm, split
            "git branch -f --delete temp",                       # union arm, split
            "cd /repo && git branch --delete --force temp",      # arm 2 in a NON-FIRST leg (union arm abstains — literal arm load-bearing)
        ],
    )
    def test_branch_delete_arm_same_leg_still_gates(self, cmd):
        """The no-new-under-block set: `git branch` + force-delete flag
        co-occurring within ONE leg still gates in ANY leg position — including
        the CRITICAL `cd /repo && git branch -D temp` (the union arm is
        first-leg-anchored and abstains there, so ONLY the per-leg literal arm
        catches it). The quoted-separator row is why this fix is per-leg
        matching over the substrate, not a tempered-regex span (`[^&|;]*` would
        wrongly ungate it). The clustered/split spellings gate via the
        first-leg flag-condition union arm — untouched surface."""
        assert D(cmd) is True, f"NEW UNDER-BLOCK: same-leg branch-delete stopped gating: {cmd!r}"

    # --- mint parity on the preserved set (detect converted WITH the read floor) ---
    @pytest.mark.parametrize(
        "cmd",
        [
            "git branch -D temp",
            "cd /repo && git branch -D temp",
            "git branch --delete --force temp",
            "git branch --force --delete temp",
            "git branch -D temp && echo done",
            'git commit -m "a && b" && git branch -D temp',
            "bash -c 'git branch -D temp'",
            "FOO=1 git branch -D temp",
        ],
    )
    def test_gated_same_leg_forms_classify_branch_delete(self, cmd):
        """Classification parity on every gated literal-arm family: the per-leg
        detect conversion leaves each same-leg form classifying branch-delete
        exactly as the whole-command arms did (probe-verified pre-change), so no
        gated form became unmintable (gated-but-unmintable = an over-block). The
        raw line-continuation form is deliberately EXCLUDED here — its raw-seam
        classification is the ONE intended delta, pinned separately below."""
        assert OP(cmd) == "branch-delete", (
            f"gated same-leg form no longer classifies branch-delete — "
            f"gated-but-unmintable over-block: {cmd!r}"
        )

    @pytest.mark.parametrize(
        "cmd",
        [
            "git branch -Df temp",
            "git branch -fD temp",
            "git branch --delete -f temp",
            "git branch -f --delete temp",
        ],
    )
    def test_clustered_first_leg_forms_still_classify_via_union_arm(self, cmd):
        """The union-arm fallback surface is untouched on the mint side: the
        clustered/split first-leg spellings (which no literal arm matches)
        still classify branch-delete via _flag_condition_danger_op."""
        assert OP(cmd) == "branch-delete"

    def test_detect_precedence_unchanged_by_conversion(self):
        """The converted loop sits at the SAME detect position (after the
        API-merge per-leg loop, before the union-arm fallback), so force-push
        precedence over a trailing branch-delete leg is unchanged."""
        assert OP("git push --force origin main && git branch -D t") == "force-push"

    def test_raw_line_continuation_coherent_at_raw_seam(self):
        """The ONE classification change on a gated command (raw seam only,
        INTENDED): pre-fix, the raw `git branch \\<newline>-D temp` was gated
        (D=True — the read floor normalizes line continuations before matching)
        yet detect on the RAW string returned None (`.*` does not span the
        un-normalized newline) — a gated-but-unclassified coherence gap at the
        retirement observer, which calls detect on the raw executed command.
        Post-fix, _split_into_legs normalizes before slicing, so the raw seam
        now classifies branch-delete — enabling CORRECT retirement of a
        genuinely executed branch-delete (the same raw-seam coherence gain the
        force-push twin's per-leg loop delivered for its class). Every
        authorization seam pre-normalizes, so mint/read behavior for this form
        is byte-identical pre/post; this pin is the coherence gain, not drift."""
        raw = "git branch \\\n-D temp"
        assert D(raw) is True
        assert OP(raw) == "branch-delete"

    def test_reversed_order_stays_ungated_pre_existing_pin(self):
        """PRE-EXISTING-False pin, NOT a cure: all three branch-delete arms are
        forward-only (`branch` precedes the flag) — unlike close there is no
        reversed arm, so `echo -D && git branch new` was already ungated before
        the per-leg move (the identity-slice counter-mutation deliberately
        EXCLUDES this row: it does not flip). This freezes the forward-only
        property — a future reversed-arm addition (mirroring
        _CLOSE_LITERAL_ARMS' reversed member) must consciously update this pin
        and run its own cross-leg sweep."""
        assert D("echo -D && git branch new") is False
        assert OP("echo -D && git branch new") is None

    # --- non-vacuity: row-by-row two-stage counter-mutation (in-memory) ---
    @pytest.mark.parametrize(
        "cmd",
        [
            "git branch new-feature && echo -D",
            "git branch --list && echo --delete --force",
            "git branch temp && grep -D skip pattern f.txt",
            "git branch backup ; ls -D",
            "git checkout main && git branch temp && echo -D",
            "git branch new-feature || echo -D",
            "git branch new | grep -D x f.txt",
            "git branch new & echo -D",
            "git branch new |& grep -D x f.txt",
        ],
    )
    def test_branch_delete_cured_rows_non_vacuous_and_single_family(self, cmd, monkeypatch):
        """Row-by-row non-vacuity AND identity-slice faithfulness for every cured
        branch-delete form (same two-stage counter-mutation as the close/API
        sweeps):

          direction 1 (fix present): the benign compound runs free (D is False).
          direction 2 (`_slice_stripped_legs` -> identity, the pre-fix
            whole-command surface): the over-block RETURNS (D is True) —
            coupling the assertion to the per-leg partition.
          faithfulness (identity-slice + `_BRANCH_DELETE_LITERAL_ARMS`
            neutered): D returns to False — proving the whole-command flip is
            caused by the branch-delete family ALONE (no force-push/close/API/
            flag-condition co-match), so the identity-slice mutation is a
            faithful single-family pre-fix simulation for this row.

        Source-revert flip set (expected RED case-count when merge_guard_common
        is reverted to its pre-per-leg-conversion shape, runtime-verified): the
        sweep's fix-coupled cases red as EXACTLY 22 — the 9 cured rows, these 9
        counter-mutation rows, the raw-seam pin, the one-seam-two-consumers
        test, the golden-row discriminator, and the branch-delete laundering
        canary in test_merge_guard_auth_symmetry.py — while every preservation /
        parity / pre-existing-False pin stays green. A different count means the
        coupling map drifted: re-derive before trusting either number."""
        assert mgc.is_dangerous_command(cmd) is False
        monkeypatch.setattr(mgc, "_slice_stripped_legs", lambda s: [s])
        assert mgc.is_dangerous_command(cmd) is True, (
            f"whole-command mutation did not restore the pre-fix branch-delete "
            f"over-block — the cured-row assertion would be vacuous: {cmd!r}"
        )
        monkeypatch.setattr(mgc, "_BRANCH_DELETE_LITERAL_ARMS", ())
        assert mgc.is_dangerous_command(cmd) is False, (
            f"whole-command flip survived branch-delete-arm neutering — a SECOND "
            f"family co-matches, so the identity-slice mutation is NOT a faithful "
            f"single-family pre-fix simulation for this row: {cmd!r}"
        )

    def test_identity_slice_flips_detect_too_one_seam_two_consumers(self, monkeypatch):
        """The ONE leg seam serves BOTH consumers: `_split_into_legs` delegates
        to `_slice_stripped_legs`, so the SAME identity-slice mutation that
        restores the read-floor over-block also restores detect's whole-command
        classification of the cured compound — proving read floor and detect
        consume one substrate (per-leg parity is by construction, not by two
        parallel implementations that could drift)."""
        cmd = "git branch new-feature && echo -D"
        assert mgc.detect_command_operation_type(cmd) is None
        monkeypatch.setattr(mgc, "_slice_stripped_legs", lambda s: [s])
        assert mgc.detect_command_operation_type(cmd) == "branch-delete", (
            "identity-slice did not restore whole-command detect classification "
            "— detect is not consuming the shared leg seam"
        )

    def test_golden_row_split_proof_each_mechanism_isolated(self, monkeypatch):
        """SPLIT PROOF on the GOLDEN row `cd /repo && git branch -D temp`.

        WHY THE PREVIOUS SHAPE HAD TO BE RE-DERIVED, not repaired. This test used to
        neuter the literal tuple ALONE and assert the row went ungated, on the premise
        that "the union arm is first-leg-anchored and abstains here, so ONLY the tuple
        can catch it." That premise was falsified by design, not by drift: the per-leg
        filter now carries `branch-delete`, so the union loop reaches this row too and
        the tuple-only neuter no longer drops it. Loosening the old assertion would
        have hidden a real change; the proof is re-derived instead.

        WHY THE ENDPOINTS PROVE NOTHING NOW. After the widening there are TWO
        independently sufficient covering mechanisms, so:
          - `untouched -> gates` passes even if one mechanism is already silently dead
            (the survivor covers the row);
          - `both neutered -> ungated` ALSO passes if one is already dead.
        Neither endpoint can detect a silently-dead mechanism. A bare either-covers
        assertion is exactly the check that proves nothing.

        THE DISCRIMINATING ASSERTION IS THE ONE THAT ISOLATES A SINGLE MECHANISM.
        Rows 1 and 2 below each neuter ONE mechanism and require the OTHER to carry
        the row alone; that is what makes this a SPLIT proof rather than a union
        check. Row 3 neuters both and requires the row to drop — it proves nothing
        else covers the form, and it doubles as the known-bad control for rows 1-2,
        so each assertion is demonstrated failing on a known-bad input BY
        CONSTRUCTION rather than merely demonstrated passing."""
        cmd = "cd /repo && git branch -D temp"
        arms = mgc._BRANCH_DELETE_LITERAL_ARMS
        per_leg = mgc._PER_LEG_OPS
        filter_without_branch_delete = tuple(
            op for op in per_leg if op != "branch-delete"
        )
        assert filter_without_branch_delete != per_leg, (
            "VACUITY GUARD: 'branch-delete' is not in the per-leg filter, so the "
            "filter-side neuter below is a no-op and row 2 would prove nothing."
        )

        # Baseline: both mechanisms live.
        assert mgc.is_dangerous_command(cmd) is True
        assert mgc.detect_command_operation_type(cmd) == "branch-delete"

        # ROW 1 — neuter the LITERAL TUPLE only. The per-leg filter must carry it.
        monkeypatch.setattr(mgc, "_BRANCH_DELETE_LITERAL_ARMS", ())
        monkeypatch.setattr(mgc, "_PER_LEG_OPS", per_leg)
        assert mgc.is_dangerous_command(cmd) is True, (
            "FILTER SIDE IS DEAD: with the literal tuple neutered, the per-leg "
            "filter failed to gate a non-first-leg `-D`. The #1134 under-block is "
            "re-opened for every form that has no literal arm."
        )
        assert mgc.detect_command_operation_type(cmd) == "branch-delete", (
            "filter side is dead on the detect arm (read/mint would disagree)"
        )

        # ROW 2 — neuter the PER-LEG FILTER only. The literal tuple must carry it.
        monkeypatch.setattr(mgc, "_BRANCH_DELETE_LITERAL_ARMS", arms)
        monkeypatch.setattr(mgc, "_PER_LEG_OPS", filter_without_branch_delete)
        assert mgc.is_dangerous_command(cmd) is True, (
            "TUPLE SIDE IS DEAD: with `branch-delete` dropped from the per-leg "
            "filter, the literal arms failed to gate a non-first-leg `-D`. The "
            "any-leg literal floor has silently stopped carrying this family."
        )
        assert mgc.detect_command_operation_type(cmd) == "branch-delete", (
            "tuple side is dead on the detect arm"
        )

        # ROW 3 — neuter BOTH. Nothing else may cover the row. This is the
        # known-bad control that makes rows 1-2 non-vacuous.
        monkeypatch.setattr(mgc, "_BRANCH_DELETE_LITERAL_ARMS", ())
        monkeypatch.setattr(mgc, "_PER_LEG_OPS", filter_without_branch_delete)
        assert mgc.is_dangerous_command(cmd) is False, (
            "A THIRD mechanism covers this row. Rows 1-2 above are therefore "
            "vacuous — they cannot attribute coverage to the mechanism they name. "
            "Identify the third family before trusting this proof."
        )
        assert mgc.detect_command_operation_type(cmd) is None, (
            "a third mechanism classifies this row on the detect arm"
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


class TestBranchDeleteWordBoundaryMustStayOff:
    """MUST_STAY_OFF — the `\\b` on the `-D` literal arm, enforced.

    The branch-delete arm is `git\\s+...branch\\s+.*-D\\b`. The trailing `\\b` is the
    only thing stopping `-D` from matching INSIDE an ordinary branch name: any name
    whose second hyphen-separated word starts with a capital D reads as `-D` to a
    boundaryless pattern — `feature-Dashboard`, `release-December`, `*-Dev*`,
    `new-Design`.

    WHY THIS CLASS EXISTS AT ALL. "Do not touch the `\\b`" was a documented mandate
    with NOTHING enforcing it. Removing the boundary from all three occurrences
    produces a suite failure set IDENTICAL to control — zero new failures, zero
    disappeared — while gating SIX ordinary good-faith commands, including
    `git branch feature-Dashboard`, which CREATES a branch and would be classified
    `branch-delete`. That is the cardinal over-block, invisible, with a green suite.

    So this is not a check that proved nothing; it was an invariant with no check.
    These rows convert it into a visible one. Every command below is benign and
    must never gate."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "git branch feature-Dashboard",              # CREATES a branch
            'git branch --list "release-Dec*"',          # list, glob
            'git branch --list "*-Dev*"',                # list, leading glob
            "git branch --contains feature-Dashboard",   # read: which branches contain
            "git branch -m old new-Design",              # rename
            "git branch --merged release-December",      # read: merged-into filter
        ],
    )
    def test_capital_d_inside_a_branch_name_never_gates(self, cmd):
        assert D(cmd) is False, (
            "CARDINAL OVER-BLOCK: an ordinary branch name containing a hyphen "
            "followed by a capital D gated. The `-D` literal arm has lost its "
            "trailing word boundary, so `-D` is matching inside a NAME rather than "
            f"as a flag. Restore the `\\b`: {cmd!r}"
        )
        assert OP(cmd) is None, (
            f"same boundary regression, on the detect arm — note `git branch "
            f"feature-Dashboard` CREATES a branch: {cmd!r}"
        )

    def test_the_boundary_rows_are_not_vacuous(self):
        """Control: the arm this class guards still fires on a REAL `-D`. Without
        this, every row above would stay green if the branch-delete arm were
        deleted outright — passing for the wrong reason."""
        assert D("git branch -D temp") is True, (
            "the branch-delete arm no longer gates a genuine `-D`, so the "
            "boundary rows above prove nothing"
        )
        assert OP("git branch -D temp") == "branch-delete"


def _close_target(cmd):
    """The minted close-target identity for `cmd` via the REAL post path."""
    res = mint(cmd)
    assert res.context is not None, f"close did not mint (refusal={res.refusal_reason}): {cmd!r}"
    return _target_value(res.context)


def _authorizes(approve_cmd, execute_cmd):
    """True iff a token minted from `approve_cmd` authorizes `execute_cmd`."""
    res = mint(approve_cmd)
    assert res.context is not None, f"approve did not mint: {approve_cmd!r}"
    return MATCH(token_from_ctx(res.context), execute_cmd)


_PULL5 = "https://github.com/o/r/pull/5"


class TestCloseTargetMintsAllFaithfulForms:
    """`gh pr close` accepts ``{<number> | <url> | <branch>}`` — all three are
    faithful. The per-leg widening gated the url/branch forms in a non-first leg
    but ``_extract_pr_number`` is number-only, so they gated WITHOUT a mint path
    = a HEAD-introduced faithful GATED-BUT-UNMINTABLE over-block (and the same
    shape pre-existed on the first leg). `_extract_close_target` closes it by
    minting every faithful form. These rows are the MUST_FLIP cert for that.

    The no-arg form (`gh pr close -d`) is NOT here: gh refuses a close with no
    positional, so it has no faithful click and its gated-but-unmintable state is
    not an over-block (measured separately)."""

    @pytest.mark.parametrize(
        "cmd,expected",
        [
            ("gh pr close 5 -d", "5"),
            ("cd /repo && gh pr close 5 -d", "5"),
            (f"gh pr close {_PULL5} -d", "url:github.com/o/r#5"),
            (f"cd /repo && gh pr close {_PULL5} -d", "url:github.com/o/r#5"),
            ("gh pr close feature -d", "branch:feature"),
            ("cd /repo && gh pr close feature -d", "branch:feature"),
        ],
    )
    def test_every_faithful_close_form_gates_and_mints(self, cmd, expected):
        assert D(cmd) is True, f"faithful close stopped gating: {cmd!r}"
        assert _close_target(cmd) == expected, (
            f"close minted the wrong identity for {cmd!r} — recognition<=>mint "
            f"coupling (#1064) requires the identity to be exactly the target"
        )

    def test_minted_identity_is_exactly_the_target_not_a_neighbor(self):
        """#1064: the url mints the PR IN THAT url, the branch mints THAT branch —
        never a different PR. The identity is parsed from the `/pull/<N>` segment
        gh itself resolves, so it cannot name a PR the command does not close."""
        assert _close_target(f"gh pr close {_PULL5} -d") == "url:github.com/o/r#5"
        assert _close_target("gh pr close https://github.com/x/y/pull/9 -d") == "url:github.com/x/y#9"
        assert _close_target("gh pr close release/2.0 -d") == "branch:release/2.0"

    @pytest.mark.parametrize(
        "cmd,expected",
        [
            # repo/owner containing digits — the /pull/ anchor is not fooled
            ("gh pr close https://github.com/o/r-123/pull/5 -d", "url:github.com/o/r-123#5"),
            ("gh pr close https://github.com/o123/r/pull/7 -d", "url:github.com/o123/r#7"),
            # trailing path / query / fragment — (\d+)(?![\w-]) stops at the PR number
            ("gh pr close https://github.com/o/r/pull/5/files -d", "url:github.com/o/r#5"),
            ("gh pr close https://github.com/o/r/pull/5?diff=split -d", "url:github.com/o/r#5"),
            ("gh pr close https://github.com/o/r/pull/5#issuecomment-9 -d", "url:github.com/o/r#5"),
            # GitHub Enterprise host — must still mint (not an over-block)
            ("gh pr close https://github.company.com/o/r/pull/42 -d", "url:github.company.com/o/r#42"),
        ],
    )
    def test_url_number_parse_robust_against_decoy_digits(self, cmd, expected):
        """The URL identity is anchored on the literal `/pull/<N>` segment, so a
        digit in the owner/repo, a query string, a fragment, or a trailing path
        component can never be mistaken for (or extend) the PR number."""
        assert _close_target(cmd) == expected


class TestCloseTargetLaunderingRefused:
    """MINT-LAUNDERING known-bads (#1064) — each MUST be caught (REFUSE). Shown
    FAILING-on-known-bad, not just the happy path passing. The four identity
    namespaces (`5` / `url:host/o/r#N` / `branch:name`) are disjoint, and the URL
    is HOST- and repo-QUALIFIED specifically so it can never set-equal a bare
    number in a DIFFERENT repo."""

    def test_cross_repo_url_to_bare_number_refuses(self):
        """THE SHARP ONE. Approve closing PR #5 via an explicit-repo URL; execute
        a bare `gh pr close 5` — which closes PR #5 of whatever repo it runs in.
        If the url minted bare `5`, this would authorize closing PR #5 in a
        DIFFERENT repo (cross-repo target confusion). Repo-qualification blocks it."""
        assert _authorizes(f"gh pr close {_PULL5} -d", "gh pr close 5 -d") is False

    def test_cross_host_url_refuses(self):
        assert _authorizes(
            "gh pr close https://github.com/o/r/pull/5 -d",
            "gh pr close https://github.enterprise.com/o/r/pull/5 -d",
        ) is False

    def test_cross_repo_url_to_url_refuses(self):
        assert _authorizes(
            "gh pr close https://github.com/o/r/pull/5 -d",
            "gh pr close https://github.com/evil/repo/pull/5 -d",
        ) is False

    def test_branch_to_different_branch_refuses(self):
        assert _authorizes("gh pr close feature -d", "gh pr close other -d") is False

    def test_branch_to_number_refuses(self):
        assert _authorizes("gh pr close feature -d", "gh pr close 5 -d") is False

    def test_number_to_different_number_refuses(self):
        assert _authorizes("gh pr close 5 -d", "gh pr close 6 -d") is False

    def test_faithful_self_authorizes_every_form(self):
        """Non-vacuity for the REFUSE rows: the same command DOES authorize itself
        on every form, so the refusals above are discrimination, not a blanket
        deny."""
        for cmd in ("gh pr close 5 -d", f"gh pr close {_PULL5} -d", "gh pr close feature -d",
                    "cd /repo && gh pr close feature -d"):
            assert _authorizes(cmd, cmd) is True, f"faithful re-execution refused: {cmd!r}"


class TestCloseTargetBranchInertAndLegScoped:
    """Constraint B — the branch identity binds the LITERAL positional token, and
    that binding is (i) LEG-SCOPED (no cross-leg pickup) and (ii) FLAG-INERT (a
    dash-token can never be bound as the target, a quoted token binds its literal
    content and cannot inject)."""

    def test_branch_identity_is_leg_scoped(self):
        """A url/branch in a BENIGN continuation leg is never picked up as the
        close target — the extractor reads only the first executable leg. If it
        leaked, the first-leg numbered close would mis-bind the later leg's ref."""
        # first leg is the real close (mints 5); a decoy branch/url follows benign
        assert _close_target("gh pr close 5 -d && echo feature") == "5"
        assert _close_target(f"gh pr close 5 -d ; echo {_PULL5}") == "5"

    def test_dash_token_is_never_bound_as_the_target(self):
        """A flag can never become the close identity — positionals starting with
        `-` are dropped. `gh pr close --repo o/r -d` has NO positional target
        (the repo is a flag value), so it abstains (mint None), never binds a flag."""
        assert _close_target_or_none("gh pr close -d --repo o/r") is None
        assert _close_target_or_none("gh pr close --repo o/r -d") is None

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh pr close feature --repo o/r -d",     # --repo VALUE after target
            "gh pr close --repo o/r feature -d",     # --repo VALUE before target
            "gh pr close feature -R o/r -d",         # short -R value
            "gh pr close -dR o/r feature",           # clustered -dR, value is next token
            'gh pr close -c "nice work" feature -d',  # -c comment value (quoted)
            "gh pr close feature -cd",               # -c attached-cluster value
            "gh pr close --comment hello feature -d",  # --comment value
        ],
    )
    def test_value_flag_value_is_not_bound_as_the_target(self, cmd):
        """CONSTRAINT B — a VALUE-taking flag's value (`-R/--repo o/r`,
        `-c/--comment msg`) is stripped before the positional scan, so a faithful
        branch-close WITH such a flag still binds the BRANCH (never over-blocks by
        counting the value as a second positional, never mis-binds the value)."""
        assert D(cmd) is True
        assert _close_target(cmd) == "branch:feature", (
            f"a value-taking flag's value was mis-read as the close target: {cmd!r}"
        )

    def test_comment_value_that_is_a_url_is_not_read_as_the_pr(self):
        """The sharpest value-flag case: a PR URL passed as a `--comment` value
        must NOT be extracted as the close target — the real target is the branch
        positional. Value stripping happens BEFORE URL classification."""
        cmd = "gh pr close feature --comment https://github.com/o/r/pull/999 -d"
        assert _close_target(cmd) == "branch:feature", (
            "a URL inside a --comment value was mis-bound as the PR — the value "
            "strip must precede URL classification"
        )

    def test_quoted_branch_binds_its_literal_content(self):
        """A quoted branch name binds the literal string (quotes stripped); it is
        a target IDENTITY, never executed, so it cannot inject a flag or a second
        command. Two distinct quoted names stay distinct identities."""
        assert _close_target('gh pr close "feature/x" -d') == "branch:feature/x"
        assert _authorizes('gh pr close "feature/x" -d', 'gh pr close "feature/y" -d') is False

    def test_branch_shaped_like_a_flag_is_not_bound_and_does_not_inject(self):
        """A positional that LOOKS like a flag (`--force`, `-D`) is dropped by the
        dash-filter, so it is never bound as the target and cannot inject. A close
        whose only positional-looking token is dash-prefixed has NO target ->
        abstains (the command is not a faithful single-target close anyway)."""
        assert _close_target_or_none("gh pr close --force -d") is None
        assert _close_target_or_none("gh pr close -D -d") is None

    def test_branch_with_shell_metachars_binds_literal_inert_identity(self):
        """A branch name carrying shell metacharacters binds the LITERAL token as
        an identity string — it is compared, never executed, so it cannot inject a
        command. Two different such names stay distinct identities (no collision)."""
        # _executable_prefix truncates at an unquoted metachar, so a bare $(...) is
        # not a single clean positional -> abstain; a QUOTED odd name binds literal.
        assert _close_target('gh pr close "weird;name" -d') == "branch:weird;name"
        assert _authorizes('gh pr close "weird;name" -d', 'gh pr close "other;name" -d') is False

    def test_branch_shaped_like_a_url_but_not_a_pull_url_abstains(self):
        """A positional containing `://` that is NOT a github `/pull/<N>` URL is an
        unrecognized URL form -> abstain (mint None), never a garbage `branch:`
        identity. gh would reject such a target anyway."""
        assert _close_target_or_none("gh pr close https://github.com/o/r/issues/5 -d") is None
        assert _close_target_or_none("gh pr close https://evil.example/o/r/tree/main -d") is None

    def test_branch_named_like_a_number_binds_the_number_namespace(self):
        """DOCUMENTED AMBIGUITY: gh resolves a bare digit as the PR NUMBER (number
        precedes branch in `{number|url|branch}`), so a branch literally named `5`
        is indistinguishable from PR #5 to gh itself. The extractor matches that
        precedence: `gh pr close 5` binds number `5`, never `branch:5`. Not a
        collision — both spellings resolve to the same command for gh."""
        assert _close_target("gh pr close 5 -d") == "5"
        assert _close_target("gh pr close 5 -d") != "branch:5"


def _close_target_or_none(cmd):
    """Like _close_target but tolerates a non-minting (abstaining) close: returns
    the identity or None, without asserting a mint occurred."""
    res = mint(cmd)
    return _target_value(res.context) if res.context is not None else None


class TestCloseTargetExtensionNonVacuous:
    """The extractor extension must be shown LIVE: neuter it back to the
    number-only behavior and a url/branch MUST_FLIP row loses its mint. If it
    keeps minting with the extension reverted, the extension is not what carries
    these forms and every row above is vacuous."""

    def test_url_and_branch_lose_mint_when_extractor_reverted(self, monkeypatch):
        # sanity: with the extension live, url + branch mint
        assert _close_target(f"gh pr close {_PULL5} -d") == "url:github.com/o/r#5"
        assert _close_target("gh pr close feature -d") == "branch:feature"
        # revert _extract_close_target to the pre-fix number-only behavior
        monkeypatch.setattr(mgc, "_extract_close_target", mgc._extract_pr_number)
        assert _close_target_or_none(f"gh pr close {_PULL5} -d") is None, (
            "url still mints with the extractor reverted — the extension is not "
            "carrying the url form, so the MUST_FLIP rows are vacuous"
        )
        assert _close_target_or_none("gh pr close feature -d") is None, (
            "branch still mints with the extractor reverted — vacuous"
        )
        # and the NUMBER form is unaffected by the revert (it never needed the extension)
        assert _close_target_or_none("gh pr close 5 -d") == "5"


class TestApiMergeMintParity:
    """#1096 API-merge mint parity — the bidirectional cert for the additive
    per-leg detect arm (GH-API-ONLY per Option B + mutating PUT/PATCH/POST + a
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
    the no-new-under-block rows are the secondary sweep. GH-API-ONLY (Option B):
    curl/wget api-merge mints NOTHING (detect=None → mint=0) — dropped because a
    value-flag denylist over curl/wget's unbounded flag space is unsound (sec #71).
    Curl/wget merges stay READ-gated (is_dangerous=True) = the pre-existing
    gated-but-unmintable state; that structural fact is asserted in
    TestApiMergeEndpointResidualBoundary::test_curl_wget_api_merge_mints_nothing_by_construction."""

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


# The endpoint (`.../repos/o/r/pulls/N/merge`) is the target PR; a decoy
# `pulls/M/merge` in a flag VALUE or another leg must never be the bound target.
# These helpers build the mint→authorize seam once for the endpoint-position suite.
def _mint_pr(cmd):
    """Mint `cmd` and return the bound pr_number (or None if it did not mint)."""
    ctx, _reason = mint(cmd)
    return ctx.get("pr_number") if ctx is not None else None


def _authorize_standalone(approve_cmd, standalone_cmd, tmp_path):
    """Mint the approval, write the token, then run the read-side authorization
    for a DIFFERENT standalone command. Returns 'ALLOW' | 'DENY' | 'NO-MINT'."""
    ctx, _reason = mint(approve_cmd)
    if ctx is None:
        return "NO-MINT"
    write_token(ctx, token_dir=tmp_path)
    err = check_merge_authorization(standalone_cmd, token_dir=tmp_path)
    return "ALLOW" if err is None else "DENY"


class TestApiMergeEndpointPositionLaunderingClosed:
    """#1096 endpoint-position fix (commit that added `_api_merge_leg_endpoint`) —
    the REMEDIATION cert for the BLOCKING target-confusion laundering the prior
    (flat first-match) extractor allowed.

    ROOT: `_extract_api_merge_pr` did `re.search(r"pulls/(\\d+)/merge", command)` —
    a FLAT whole-string first-match that bound the FIRST `pulls/<M>/merge`
    substring, which need not be the endpoint. A decoy PR in a flag body
    (`-f note=pulls/5/merge`), a header, the `-R` value, or an earlier leg
    (`echo pulls/5/merge && …`) bound a token `{merge, 5}`; a standalone
    `gh api -X PUT .../pulls/5/merge` (NEVER approved) then AUTHORIZED — a consent
    breach (approve merge-6 → authorize merge-5).

    FIX: bind the ENDPOINT-position PR (the URL positional the command actually
    merges) via the shared per-leg helper + shlex-view positional walk. Every row
    below approves endpoint-6 and asserts (a) the token binds 6, and (b) the
    standalone decoy-5 merge DENIES for lack of a matching token — the laundering
    is CLOSED. Priority: this is the laundering-closed (security) direction; the
    no-over-block (§0 inviolable) direction is the sibling class below."""

    # (name, approval command [endpoint 6, decoy 5 embedded], standalone decoy-5)
    _DECOY = "gh api -X PUT repos/o/r/pulls/5/merge"

    @pytest.mark.parametrize(
        "approve,standalone",
        [
            ("echo pulls/5/merge && gh api -X PUT repos/o/r/pulls/6/merge", _DECOY),
            ("gh api -X PUT -f note=pulls/5/merge repos/o/r/pulls/6/merge", _DECOY),
            ('gh api -X PUT -f "note=pulls/5/merge" repos/o/r/pulls/6/merge', _DECOY),
            ("gh api -X PUT -f note='pulls/5/merge' repos/o/r/pulls/6/merge", _DECOY),
            ('gh api -H "X-Ref:pulls/5/merge" -X PUT repos/o/r/pulls/6/merge', _DECOY),
            ("gh -R pulls/5/merge api repos/o/r/pulls/6/merge -X PUT", _DECOY),
            ("gh api -X PUT -f x=pulls/5/merge repos/o/r/pulls/6/merge", _DECOY),
        ],
    )
    def test_decoy_binds_endpoint_and_standalone_decoy_denies(
        self, approve, standalone, tmp_path
    ):
        """Every confirmed exploit: the approval binds the ENDPOINT (6), and the
        never-approved standalone decoy-5 merge DENIES. Pre-fix the flat
        first-match bound 5 and this standalone AUTHORIZED (the laundering)."""
        assert _mint_pr(approve) == "6", (
            f"endpoint-position bind regressed (bound a decoy): {approve!r}"
        )
        assert _authorize_standalone(approve, standalone, tmp_path) == "DENY", (
            f"LAUNDERING OPEN: standalone decoy-5 authorized off the {approve!r} token"
        )

    def test_base_no_token_denies_standalone_decoy(self, tmp_path):
        """Load-bearing base check: with NO token minted, the standalone decoy-5
        merge DENIES — so the pre-fix ALLOW was caused by the mis-bound token,
        not an ambient allow. This isolates the mis-bind as the laundering cause."""
        err = check_merge_authorization(self._DECOY, token_dir=tmp_path)
        assert err is not None, "standalone api-merge must DENY with no token"

    def test_laundering_closed_non_vacuous_under_flat_extractor(self, monkeypatch):
        """NON-VACUITY (in-memory): the #1096 endpoint-position walk is LOAD-BEARING,
        proven INDEPENDENTLY of the #1178 inert-value strip. The mint now isolates the
        destructive leg via the read-symmetric extraction surface, whose leg-strip
        replaces the `-f note=…` body decoy with 'STRIPPED' UPSTREAM — a SECOND,
        independent laundering-closure layer. So a flat-extractor mutation can no longer
        re-bind the decoy through the mint path (the strip removed it); to isolate the
        WALK's coupling we restore the pre-fix flat first-match extractor and assert it
        RE-BINDS the decoy 5 on the RAW extraction surface (`extract_command_context` of
        the whole command — the surface the walk is defined against, where the strip is
        not in the path). direction-2b then pins the LAYERED defense: through the stripped
        mint path the same flat extractor binds the endpoint 6, so EITHER layer alone
        closes the laundering. The module-global `_extract_api_merge_pr` binding resolves
        at call time."""
        approve = "gh api -X PUT -f note=pulls/5/merge repos/o/r/pulls/6/merge"
        # direction 1 — fix present: the walk binds the ENDPOINT on both surfaces.
        assert mgc.extract_command_context(approve).get("pr_number") == "6"
        assert _mint_pr(approve) == "6"
        # restore the pre-fix flat first-match extractor.
        def flat(command):
            m = _real_re.search(r"pulls/(\d+)/merge\b", command)
            return m.group(1) if m else None
        monkeypatch.setattr(mgc, "_extract_api_merge_pr", flat)
        # direction 2a — WALK non-vacuity, ISOLATED from the strip: on the RAW extraction
        # surface (strip not in the path) the flat first-match re-binds the decoy 5,
        # proving the endpoint-position walk — not vacuity — is what binds 6 above.
        assert mgc.extract_command_context(approve).get("pr_number") == "5", (
            "flat-extractor mutation did not re-bind the decoy on the RAW extraction "
            "surface — the endpoint-position walk assertions would be vacuous"
        )
        # direction 2b — LAYERED defense (the #1178 leg-strip): through the stripped mint
        # path the `-f` body decoy is stripped to 'STRIPPED' upstream, so even the flat
        # extractor binds the endpoint 6 — the strip closes the laundering INDEPENDENTLY
        # of the walk. (Pre-strip, this bound the decoy 5; the additional layer is why
        # the mint path can no longer exhibit the flat extractor's mis-bind.)
        assert _mint_pr(approve) == "6", (
            "the #1178 inert-value strip must independently close the -f body decoy in "
            "the mint path even when the endpoint-position walk is defeated"
        )


class TestApiMergeEndpointNoOverBlock:
    """#1096 endpoint-position fix — the §0 INVIOLABLE no-over-block direction:
    every faithful GH-API api-merge binds its CORRECT endpoint and round-trips, and
    the fix never returns None for a recognized merge (no gated-but-unmintable).
    GH-API-ONLY (Option B): curl/wget api-merge is intentionally unmintable — see
    TestApiMergeEndpointResidualBoundary::test_curl_wget_api_merge_mints_nothing_by_construction."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api -X PUT repos/o/r/pulls/6/merge",
            "gh api --method PATCH repos/o/r/pulls/6/merge",
            "gh api repos/o/r/pulls/6/merge -X POST",          # method-after-path
            "gh -R o/r api repos/o/r/pulls/6/merge -X PUT",     # global-flag
            "gh api -X PUT -f note=pulls/5/merge repos/o/r/pulls/6/merge",  # body-COINCIDENCE faithful
        ],
    )
    def test_faithful_spelling_binds_correct_endpoint(self, cmd):
        """Every faithful spelling mints its CORRECT endpoint (6), including the
        body-coincidence merge whose `-f note` happens to mention pulls/5 — the
        endpoint positional (6) is bound, never the body decoy, and never None."""
        assert _mint_pr(cmd) == "6", f"faithful api-merge bound wrong/None: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api -X PUT repos/o/r/pulls/6/merge",
            "gh api -X PUT -f note=pulls/5/merge repos/o/r/pulls/6/merge",
        ],
    )
    def test_faithful_round_trip_authorizes(self, cmd, tmp_path):
        """The faithful click round-trips: approve → mint → byte-identical
        re-execution AUTHORIZES (the §0 inviolable guarantee)."""
        assert _authorize_standalone(cmd, cmd, tmp_path) == "ALLOW", (
            f"faithful api-merge round-trip over-blocked: {cmd!r}"
        )

    def test_tokenizer_failure_still_binds_endpoint(self):
        """Over-block-safe fallback: an unbalanced-quote faithful merge (shlex
        tokenizer returns None) still binds the endpoint from the stripped/raw
        first-match — NEVER None for a recognized leg (a mis-parse must not gate
        a faithful merge)."""
        cmd = 'gh api -X PUT -f note="unbalanced repos/o/r/pulls/6/merge'
        assert OP(cmd) == "merge"
        assert _mint_pr(cmd) == "6", "tokenizer-failure fallback returned None (over-block)"

    def test_gh_api_attached_method_is_1079_underblock_not_overblock(self):
        """DOCUMENTED BOUNDARY (not an over-block): the gh-api ATTACHED method
        spelling `gh api --method=PUT .../merge` is is_dangerous=False (the read
        floor's `(?:-X|--method)\\s+` requires whitespace) yet OP=merge — the
        pre-existing #1079 method-delimiter under-block (attached/`=` spellings
        bypass the read floor; closed won't-fix). It is READ==MINT SYMMETRIC:
        not gated → correctly NOT mintable, so it is NOT a gated-but-unmintable
        over-block, and it is unchanged by the endpoint-position fix. The SPACED
        gh-api form and wget's native `--method=` form DO gate + mint (covered
        above). This row pins the symmetry so a future 'harden' that mints the
        attached form without also gating it (asymmetry = over-block-shaped
        surprise) turns red."""
        cmd = "gh api --method=PUT repos/o/r/pulls/6/merge"
        assert D(cmd) is False, "read floor unexpectedly gates the attached form"
        assert _mint_pr(cmd) is None, (
            "attached-method form minted while the read floor does NOT gate it — "
            "read/mint asymmetry (see #1079); this is the symmetry tripwire"
        )


class TestApiMergeEndpointCarrierInteraction:
    """#1096 §7 carrier-interaction (b409be63 L8 / #1074): the PATH-resident
    endpoint survives EVERY value-stripping carrier (carrier-4 var-assign strip +
    carrier-8 HTTP-body strip), a body decoy is removed exactly once, and the
    §2.3 unquoted carrier-8 extension does not disturb the endpoint. Two levels:
    (a) the mechanism (is_dangerous/extract), (b) the end-to-end mint→authorize
    security proof."""

    def test_mechanism_endpoint_survives_both_carriers_decoy_removed(self):
        """MECHANISM: a `-f branch=pulls/5/merge` body decoy alongside the
        endpoint pulls/6/merge — the endpoint (path-resident) survives the
        carrier strip and the extractor binds 6; the body decoy is not bound.
        The command still gates (is_dangerous=True — the endpoint is a real
        mutating merge) with its target intact."""
        cmd = "gh api -X PUT -f branch=pulls/5/merge repos/o/r/pulls/6/merge"
        assert D(cmd) is True, "endpoint merge stopped gating after carrier strip"
        assert mgc._api_merge_leg_endpoint(cmd) == "6", "carrier strip disturbed the endpoint"

    def test_seam_body_decoy_does_not_leak_to_mintable_token(self, tmp_path):
        """SECURITY: end-to-end, the body decoy does not leak through to a
        mintable token — approving the endpoint-6 merge binds 6, and a standalone
        decoy-5 merge DENIES (the decoy never became an authorizing target)."""
        approve = "gh api -X PUT -f branch=pulls/5/merge repos/o/r/pulls/6/merge"
        assert _mint_pr(approve) == "6"
        assert _authorize_standalone(
            approve, "gh api -X PUT repos/o/r/pulls/5/merge", tmp_path) == "DENY"

    def test_contents_capital_c_write_to_main_still_gates(self):
        """CONTENTS case-insensitivity under-block (independent of the coder's own
        test_gh_api_put_contents_main_case_insensitive): the §2.3 unquoted body
        strip exposed a latent under-block — carrier-8's `contents/` preservation
        guard was case-SENSITIVE while its read arm is case-insensitive, so a
        capital-`Contents/` write-to-main had its main/master gating body stripped
        → is_dangerous=False → a dangerous contents-write ran ungated. The IGNORECASE
        fix restores gating. This asserts the under-block is CLOSED."""
        cmd = "gh api -X PUT repos/o/r/Contents/README.md -f branch=Main"
        assert D(cmd) is True, (
            "capital-C Contents write-to-main is UNGATED — the case-sensitivity "
            "under-block reopened (the §2.3 fix regressed)"
        )

    def test_contents_case_fix_introduces_no_over_block(self):
        """The other direction of the IGNORECASE change: a lowercase faithful
        contents write-to-main still gates + classifies (the fix only PRESERVES
        more spans from stripping, so it cannot introduce an over-block — this
        pins that it did not accidentally change the lowercase behavior)."""
        cmd = "gh api -X PUT repos/o/r/contents/README.md -f branch=main"
        assert D(cmd) is True


class TestApiMergeEndpointResidualBoundary:
    """#1096 GH-API-ONLY narrowing (Option B) TRIPWIRE. The prior option-1 exotic-curl
    ACCEPTED-RESIDUAL is SUPERSEDED: curl/wget api-merge legs now classify detect=None
    (mint=0), so there is NO laundering residual to accept — the whole curl/wget decoy
    class dies BY CONSTRUCTION (can't mint -> can't launder; sec #71 proved a value-flag
    denylist over curl/wget's unbounded flag space is uncompletable). This class pins
    (a) the gh-api no-widening guard (the kept, provably-sound client) and (b) the
    STRUCTURAL assertion that curl/wget mint NOTHING regardless of flags — deliberately
    NOT re-enumerating the 52 curl vectors as a denylist (that would re-import the
    open-endedness Option B exits)."""

    # (a) gh-api no-widening: an ENUMERATED gh-api value-flag carrying a decoy URL BEFORE
    # the endpoint is skipped, so mint binds the ENDPOINT (6) and the standalone decoy-5
    # DENIES. RED if a common gh-api flag drops from _API_MERGE_GH_VALUE_FLAGS (widening).
    @pytest.mark.parametrize(
        "approve",
        [
            'gh api -H "X-Ref: https://x/repos/o/r/pulls/5/merge" -X PUT repos/o/r/pulls/6/merge',
            "gh api -f note=https://x/repos/o/r/pulls/5/merge -X PUT repos/o/r/pulls/6/merge",
            "gh api -F note=https://x/repos/o/r/pulls/5/merge -X PUT repos/o/r/pulls/6/merge",
            "gh api -t https://x/repos/o/r/pulls/5/merge -X PUT repos/o/r/pulls/6/merge",
        ],
    )
    def test_gh_api_enumerated_value_flag_decoy_binds_endpoint(self, approve, tmp_path):
        """gh-api no-widening: an enumerated gh-api value-flag decoy binds the ENDPOINT
        (6), and the standalone decoy-5 DENIES. RED if a common gh-api flag drops from
        the skip set (silent widening)."""
        assert _mint_pr(approve) == "6", (
            f"a gh-api enumerated value-flag decoy bound the decoy — residual widened: {approve!r}"
        )
        assert _authorize_standalone(
            approve, "gh api -X PUT repos/o/r/pulls/5/merge", tmp_path) == "DENY"

    @pytest.mark.parametrize(
        "cmd",
        [
            # canonical curl/wget merge forms
            "curl -X PUT https://api.github.com/repos/o/r/pulls/6/merge",
            "curl --request PUT https://api.github.com/repos/o/r/pulls/6/merge",
            "wget --method=PUT https://api.github.com/repos/o/r/pulls/6/merge",
            "curl --url https://api.github.com/repos/o/r/pulls/6/merge -X PUT",
            # non-vacuity sample of the sec-#71 decoy forms — also None (NOT re-enumerating 52)
            'curl -H "X: https://x/pulls/5/merge" -X PUT https://api.github.com/repos/o/r/pulls/6/merge',
            "curl --request https://api.evil/pulls/5/merge -X PUT https://api.github.com/repos/o/r/pulls/6/merge",
            "wget -O /tmp/pulls/5/merge --method=PUT https://api.github.com/repos/o/r/pulls/6/merge",
        ],
    )
    def test_curl_wget_api_merge_mints_nothing_by_construction(self, cmd):
        """(b) THE STRUCTURAL narrowing assertion (Option B): a curl/wget api-merge leg
        classifies detect=None -> mint=0, regardless of flags — so the ENTIRE curl/wget
        decoy class (the 52 sec-#71 vectors) is DEAD by construction (can't mint -> can't
        launder). Still READ-gated (is_dangerous True) = the pre-existing
        gated-but-unmintable state, NOT a new over-block/under-block. Replaces the old
        'curl decoy closed' pins, whose real reason is now 'curl can't mint at all'."""
        assert OP(cmd) is None, f"curl/wget api-merge classified (narrowing breached): {cmd!r}"
        assert _mint_pr(cmd) is None, f"curl/wget api-merge minted (narrowing breached): {cmd!r}"
        assert D(cmd) is True, f"curl/wget api-merge no longer read-gated (under-block): {cmd!r}"

    def test_gh_api_unknown_flag_non_url_value_no_over_block(self):
        """No-over-block / never-None on the KEPT client (gh-api): a faithful gh-api merge
        with an UNKNOWN flag carrying a NON-URL benign value still binds the endpoint (6),
        never None. RED if a future 'harden' fail-closes on multi-token/exotic forms
        (reintroducing a cardinal-sin over-block for gh-api)."""
        cmd = "gh api -X PUT repos/o/r/pulls/6/merge --some-unknown-flag benign"
        assert _mint_pr(cmd) == "6", (
            "faithful gh-api merge with an unknown benign flag returned wrong/None — a "
            "harden fail-closed into an over-block"
        )


class TestAcceptedRecognitionLimitationPins:
    """FORWARD-PROTECTION pins for the recognition posture.

    THE PRINCIPLE these pins protect is unchanged and is the reason they exist:
    over-blocking a faithful click is WRONG BY DEFINITION, worse than missing a
    buried op, and the fix for any over-block WIDENS the mint rather than narrowing
    detection into a new under-block.

    WHAT CHANGED — read this before restoring anything from git history. These pins
    were originally written to assert that non-first-leg push-delete / mass-delete /
    cluster-flag forms run UNGATED, on the premise that reaching them REQUIRED a
    match-anywhere scan (which would fire on a quoted `:ref` / `--mirror` mention in
    a benign leg and over-block a faithful click). THAT PREMISE WAS FALSE. A quote-
    aware PER-LEG predicate reaches those forms with no match-anywhere behavior: each
    call sees ONE isolated leg and derives flags AND positionals from it, so a mention
    inside a benign leg stays a mention. Leaving them ungated was therefore not a
    principled limitation but a good-faith-reachable UNDER-BLOCK (`cd /repo && git
    push origin --delete feature` ran completely ungated), and it was closed.

    So the OUTCOME assertion those pins carried was a PROXY for the author's real
    intent — protect faithful clicks — and the proxy broke when the mechanism it
    stood on was replaced. The pins below now assert the intent directly: the
    MECHANISM that would genuinely over-block is still absent / the property that
    makes per-leg safe is still present, PLUS benign rows that must stay ungated.

    STILL TRUE, and still the thing to guard: the LITERAL arms' word boundary must
    not be loosened. That WOULD grant delete tokens match-anywhere coverage and IS
    the over-block this class was created to prevent.
    """

    def test_no_match_anywhere_literal_arm_for_delete_tokens(self):
        """MECHANISM PIN #1 — asserts the ABSENCE of the one unsafe mechanism.

        The over-block this class guards comes from ONE specific shape: a literal arm
        that matches a delete token ANYWHERE in the command string, with no word
        boundary and no leg isolation. Such an arm fires on a delete token sitting
        inside a benign leg — a quoted `--mirror` in a push option, a `:main` inside a
        commit message — and blocks a faithful click.

        Non-first-leg reach is NOT that shape and must not be confused with it: it is
        the SAME quote-aware predicate re-invoked on an ISOLATED leg. This pin
        therefore constrains the literal arms only, and is deliberately silent about
        per-leg coverage.

        Worded as an ABSENCE claim (contrast mechanism pin #2, which asserts a
        PRESENCE): the two failure modes differ, and one shared pin would paper over
        that difference.

        NOT a regex-shape assertion. Inspecting the arm patterns for word boundaries
        proves nothing here — every arm carries `\\b` in its leading `\\bgit` anchor, so
        a 'pattern contains \\b' check passes unconditionally. Match-anywhere is a
        property of HOW the arms are APPLIED, not of what they contain, so this pin
        measures the application.

        The row is chosen so the arm regex ITSELF spans the leg separator: the
        branch-delete arm is `git\\s+...branch\\s+.*-D\\b`, and its `.*` happily crosses
        `&&`, so it MATCHES the whole compound below. The guard nonetheless returns
        False — which is only possible if the arms are evaluated PER-LEG. That makes
        the pin self-controlling: the first assertion establishes the arm would fire
        whole-string (if it ever stops, the premise moved and you are told so rather
        than passing silently), and the second establishes it does not."""
        cmd = "git branch new-feature && echo -D"
        assert any(arm.search(cmd) for arm in mgc._BRANCH_DELETE_LITERAL_ARMS), (
            "PREMISE MOVED: no branch-delete arm matches this compound whole-string "
            "any more, so this row no longer discriminates per-leg from "
            "match-anywhere. Re-derive the row before trusting the pin below."
        )
        assert mgc.is_dangerous_command(cmd) is False, (
            "MATCH-ANYWHERE REGRESSION: a branch-delete arm whose regex spans the leg "
            "separator now gates a command whose only `-D` sits in a BENIGN `echo` "
            "leg. The literal arms are being applied to the WHOLE command instead of "
            "per-leg — this is the faithful-click over-block this class exists to "
            "prevent."
        )
        assert mgc.detect_command_operation_type(cmd) is None, (
            "same match-anywhere regression, on the detect side"
        )

    @pytest.mark.parametrize(
        "cmd",
        [
            # A delete token quoted inside a benign push OPTION — the canonical
            # match-anywhere casualty.
            'git push origin feature -o "note: use --mirror for backups"',
            'git push origin feature -o "cleanup: replaces --delete flow"',
            # A delete token inside a commit message on a benign leg.
            'git commit -m "document --mirror and :main semantics"',
            # A delete token echoed, never executed.
            'echo "run git push origin :main to drop it"',
            # Reads that merely NAME the destructive spellings.
            "git help push | grep -- --mirror",
        ],
    )
    def test_delete_token_in_benign_context_stays_ungated(self, cmd):
        """OUTCOME ROWS for mechanism pin #1 — the faithful clicks the absent
        mechanism would have blocked.

        Mechanism-only is weaker than it looks: it can be fully satisfied while an
        over-block returns by another route. These rows assert the INTENT (a faithful
        click is never blocked) rather than a proxy for it, so they stay valid even if
        the mechanism is later re-expressed some other way. Every row names a delete
        token but executes nothing destructive."""
        assert D(cmd) is False, (
            f"OVER-BLOCK: a delete token in a BENIGN context gated a faithful click. "
            f"Something acquired match-anywhere reach over delete tokens: {cmd!r}"
        )
        assert OP(cmd) is None, f"benign delete-token mention classified as an op: {cmd!r}"

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
        above): the LITERAL danger arms (the per-leg literal-arm tuples — force-push,
        branch -D, the API ref/protection arms — plus the DANGEROUS_PATTERNS gh pr
        merge arm) match in ANY leg position (per-leg for the tuple arms) and STILL
        gate in a NON-FIRST leg. This proves the accepted-ungated set is SPECIFIC to the
        parse-dependent union-arm push forms — NOT a general compound bypass — so the
        accepted set cannot silently widen. If a literal arm ever stops gating in a
        non-first leg, that is a real under-block and this turns red."""
        assert D(cmd) is True, f"UNDER-BLOCK: literal arm stopped gating in a non-first leg: {cmd!r}"
        assert OP(cmd) == expected_op

    def test_leg_locality_invariant_flags_and_positionals_share_one_leg(self):
        """MECHANISM PIN #2 — asserts the PRESENCE of the property that makes
        per-leg coverage safe. This is the STRONGER of the two pins.

        Deliberately worded as a PRESENCE claim, unlike pin #1's absence claim. The
        failure modes are not the same and a single shared pin would hide the
        difference: pin #1 guards against a mechanism being ADDED (match-anywhere
        reach), this one guards against a property being REMOVED (leg locality).

        THE INVARIANT: `_flag_condition_danger_op(leg)` sets
        `prefix = _executable_prefix(leg)` and derives EVERYTHING from that one
        prefix — the token list, the coarse op-shape, and the extractor inputs. So
        flags AND positionals come from the SAME isolated leg. This is what makes it
        safe for callers to re-invoke the arm per leg: the cross-leg flag leak
        (positionals from leg 1, flags from the whole command, so a `--force` in a
        benign continuation mislabels a benign first-leg op) is impossible BY
        CONSTRUCTION, not by an ordering accident.

        If this property is ever removed, per-leg invocation becomes actively unsafe
        and the widened `_PER_LEG_OPS` filter turns into an over-block engine —
        which is why this pin is stronger than pin #1 and why it must fail loudly."""
        # Positionals name a BENIGN op; the danger flag lives in a LATER leg.
        # If flags leaked from the whole command, leg 1 would be mislabeled.
        leaky = "git branch new-feature && echo --delete --force"
        assert mgc._flag_condition_danger_op(leaky) is None, (
            "CROSS-LEG FLAG LEAK: the union arm classified a benign first-leg "
            "`git branch new-feature` as dangerous by picking up `--delete --force` "
            "from a LATER leg. Flags are no longer derived from the same isolated "
            "leg as the positionals — per-leg invocation is now unsafe."
        )
        # Control: the SAME flags, in the SAME leg as the positionals, DO fire.
        # Without this, the assertion above passes even if the arm stopped
        # recognizing the op entirely (verified presence, not function).
        same_leg = "git branch --delete --force temp"
        assert mgc._flag_condition_danger_op(same_leg) == "branch-delete", (
            "VACUITY CONTROL FAILED: the union arm no longer recognizes "
            "`--delete --force` even within ONE leg, so the leak assertion above "
            "proves nothing. Re-derive both rows."
        )
        # And the invariant survives leg isolation: the danger leg, taken alone,
        # still classifies — this is exactly what the per-leg callers rely on.
        assert mgc._flag_condition_danger_op("git branch -Df temp") == "branch-delete"

    @pytest.mark.parametrize(
        "cmd",
        [
            # Benign first leg + a danger-flag SPELLING in a later, non-executing leg.
            "git branch new-feature && echo --delete --force",
            "git branch --list && echo -Df",
            "gh pr view 5 && echo --delete-branch",
            # Flag-shaped text as an argument VALUE on a benign command.
            "git config --get alias.nuke",
            "grep -Df pattern file.txt",
        ],
    )
    def test_flags_in_a_benign_leg_never_label_a_benign_op(self, cmd):
        """OUTCOME ROWS for mechanism pin #2 — the faithful clicks a leak would block.

        Pairs with the mechanism assertion above for the same reason pin #1 carries
        outcome rows: a mechanism pin can be fully satisfied while an over-block
        returns by another route. Each row has a benign executable op and a danger
        flag that must stay inert because it is not in that op's leg."""
        assert D(cmd) is False, (
            f"OVER-BLOCK: a danger flag in a NON-EXECUTING leg labeled a benign op "
            f"as dangerous — the cross-leg flag leak is back: {cmd!r}"
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
