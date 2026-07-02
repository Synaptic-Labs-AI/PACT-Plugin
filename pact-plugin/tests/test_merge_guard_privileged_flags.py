"""Privileged-flag binding RED matrix + CLI-form normalization.

SACROSANCT merge-gate control (risk tier CRITICAL). Before this binding, the
guard bound an approval to its execution by (operation_type, target) only and
DROPPED every dash-flag — so a privileged flag added at execution time rode past
the checkpoint undetected:

    approve `gh pr merge 5`  →  execute `gh pr merge 5 --admin`     (branch-protection bypass)
    approve `gh pr merge 5`  →  execute `gh pr merge 5 -R victim/x` (cross-repo redirect)
    approve `git push origin main --force` → execute `... --no-verify` (pre-push-hook bypass)

The fix adds a `bound_flags` binding dimension enforced as a never-escalate
SET-EQUALITY rule: the executed command's binding-relevant flags must EXACTLY
equal the approved set, so ANY added privilege OR dropped constraint REFUSES.

Layering (which guard each test exercises):
  * READ ARM — `_token_matches_command(token, command)`: the executed `command`
    is scanned LIVE by the real `extract_command_context` SSOT; only the approval
    (token) side is hand-built. Asserts REFUSE on any flag difference and
    AUTHORIZE on an exact, FORM-INVARIANT match.
  * SCANNER — `extract_privileged_flags(command, op_type)`: direct output pins for
    the CLI-form normalization (short / long / =-joined / attached-short /
    combined-short cluster / git-surface prefix abbreviation), incl. the pflag
    `-Rd` value-consumption subtlety and the git-vs-gh abbreviation asymmetry.
  * MULTIPLICITY — `_collect_pairs`: flag variation is a pair ATTRIBUTE, never part
    of (op,target) identity, so it cannot inflate the distinct-pair count and trip
    the SACROSANCT divergence-refusal.

The A1 MINT symmetry (the mint must scan a surface WIDER than the
quote-truncated bare-command region — the command's own leg within the
selected-option text, so a flag after a quoted argument still binds) is proven
through the REAL mint seam in test_merge_guard_auth_symmetry.py — a hand-built
token would bypass the mint widening and be vacuous for that claim.

Non-vacuity (counter-test-by-revert; measured cardinality in the TEST HANDOFF):
  * The read-arm REFUSE matrix is coupled to the C2 set-equality gate in
    merge_guard_pre.py. A SOURCE-ONLY revert of that gate makes every REFUSE flip
    to AUTHORIZE → these tests go RED. (They also die under a C1 revert, which
    removes the `bound_flags` key entirely — but C2 is the clean source-only
    target since it does not touch the scanner module.) The behavioral tests here
    import only STABLE symbols, so they still COLLECT under either revert.
  * The scanner-form output pins are coupled to the C1 scanner in
    merge_guard_common.py: a form the scanner mishandles changes the asserted
    canonical token → RED at HEAD. The git-vs-gh asymmetry control pins the
    security-load-bearing `is_git_surface` branch (a missed git abbreviation is an
    UNDER-block). A C1 source-only revert removes `extract_privileged_flags`
    (ImportError), so these pins isolate the scanner by exact output, not revert.
"""

import pytest


def _refuses(token_context: dict, command: str) -> bool:
    """True iff the read arm REFUSES (does not authorize) `command` for a token
    carrying `token_context`. The command's bound_flags are scanned LIVE by the
    production SSOT; only the approval side is hand-built."""
    from merge_guard_pre import _token_matches_command

    return not _token_matches_command({"context": token_context}, command)


def _authorizes(token_context: dict, command: str) -> bool:
    from merge_guard_pre import _token_matches_command

    return _token_matches_command({"context": token_context}, command)


# ════════════════════════════════════════════════════════════════════════════
# READ ARM — the bypass matrix: every privileged-flag difference REFUSES
# ════════════════════════════════════════════════════════════════════════════


class TestReadArmBypassRefuses:
    """A privileged flag ADDED at execution past a flagless (or differently-
    flagged) approval REFUSES — the headline #1042 bypasses, across every CLI
    form and op-class."""

    # (id, approved-token-context, executed-command)
    _BYPASS = [
        # --admin (branch-protection bypass) added past a flagless approval.
        ("admin_add_bf_empty",
         {"operation_type": "merge", "pr_number": "5", "bound_flags": []},
         "gh pr merge 5 --admin"),
        # A pre-fix token with NO bound_flags key defaults to the empty set, so a
        # privileged execution still mismatches (over-block-safe; tokens expire).
        ("admin_add_no_key",
         {"operation_type": "merge", "pr_number": "5"},
         "gh pr merge 5 --admin"),
        # -R/--repo cross-repo redirect — the headline bug — in every form.
        ("repo_redirect_global_long",
         {"operation_type": "merge", "pr_number": "5", "bound_flags": []},
         "gh --repo victim/repo pr merge 5"),
        ("repo_redirect_attached_short",
         {"operation_type": "merge", "pr_number": "5", "bound_flags": []},
         "gh pr merge 5 -Rvictim/repo"),
        ("repo_redirect_equals_joined",
         {"operation_type": "merge", "pr_number": "5", "bound_flags": []},
         "gh pr merge 5 --repo=victim/repo"),
        # SHORT =-joined (-R=value) — a DISTINCT scanner branch from the long
        # =-joined form: the short-cluster value path strips a leading `=` off the
        # cluster remainder. Exercises that `=`-strip (otherwise uncovered).
        ("repo_redirect_short_equals_joined",
         {"operation_type": "merge", "pr_number": "5", "bound_flags": []},
         "gh pr merge 5 -R=victim/repo"),
        # Approved one repo, executed a DIFFERENT repo → still REFUSE.
        ("repo_redirect_different_repo",
         {"operation_type": "merge", "pr_number": "5", "bound_flags": ["--repo=a/x"]},
         "gh -R b/y pr merge 5"),
        # -d/--delete-branch on MERGE is a post-merge side-effect (deletes the
        # source branch) — bound on the merge op-class.
        ("delete_branch_add_short",
         {"operation_type": "merge", "pr_number": "5", "bound_flags": []},
         "gh pr merge 5 -d"),
        # --no-verify (pre-push-hook bypass) added on a force-push.
        ("no_verify_add",
         {"operation_type": "force-push", "target_ref": "main", "bound_flags": []},
         "git push --no-verify origin main --force"),
        # Cross-repo redirect on the CLOSE op-class.
        ("close_repo_redirect",
         {"operation_type": "close", "pr_number": "5", "bound_flags": []},
         "gh -R victim/repo pr close 5"),
        # --force-with-lease added past a PLAIN push-to-main approval (#1064 fold:
        # plain and lease pushes share the push-to-main op-class; this presence
        # bind is what separates their token identities — the lease push CAN
        # rewrite history, so a plain approval must never authorize it).
        ("lease_add_past_plain_push",
         {"operation_type": "push-to-main", "target_ref": "main", "bound_flags": []},
         "git push --force-with-lease origin main"),
        # =-joined spelling binds the SAME canonical bare token (boolean bind) —
        # still a set mismatch against the plain approval.
        ("lease_value_add_past_plain_push",
         {"operation_type": "push-to-main", "target_ref": "main", "bound_flags": []},
         "git push --force-with-lease=main:abc123 origin main"),
    ]

    @pytest.mark.parametrize(
        "ctx,command", [(c, cmd) for (_id, c, cmd) in _BYPASS],
        ids=[c[0] for c in _BYPASS],
    )
    def test_added_privileged_flag_refuses(self, ctx, command):
        assert _refuses(ctx, command)

    def test_git_no_verif_abbreviation_refuses(self):
        """SECURITY-LOAD-BEARING: git's parse-options accepts an unambiguous
        long-prefix ABBREVIATION, so `git push --no-verif ...` REALLY disables the
        pre-push hook. Matching only the exact `--no-verify` would scan the
        abbreviation to [] → [] == [] → AUTHORIZE → a silent UNDER-block. The
        git-surface prefix expansion (--no-verif → --no-verify) makes the executed
        set {--no-verify} != {} → REFUSE. This is the one normalization gap whose
        omission under-blocks rather than over-blocks (architect §7.1)."""
        assert _refuses(
            {"operation_type": "force-push", "target_ref": "main", "bound_flags": []},
            "git push --no-verif origin main --force",
        )

    def test_git_no_verify_short_prefix_abbreviation_refuses(self):
        """Even a very short unambiguous prefix (`--no-v`, `--no`) of the sole
        force-push denylist flag expands and REFUSES — the abbreviation closure is
        not limited to a one-character truncation."""
        assert _refuses(
            {"operation_type": "force-push", "target_ref": "main", "bound_flags": []},
            "git push --no-v origin main --force",
        )


class TestReadArmCombinedShortClusterRefuses:
    """The combined-short cluster is the under-block the lead required CLOSED (not
    shipped-and-flagged): a boolean-only cluster parser would DROP a value-taking
    short embedded mid-cluster (the -R in `-dR owner/repo`), so an unapproved
    cross-repo redirect would ride a `-dR` cluster past a `-d`-only approval. The
    general per-character walk captures every bound short regardless of ordering."""

    def test_mixed_cluster_dR_adds_unapproved_repo_refuses(self):
        """Approved `-d` (delete-branch) only; executed `-dR owner/repo` adds an
        unapproved --repo=owner/repo → REFUSE. (The cluster yields
        {--delete-branch, --repo=owner/repo} vs the approved {--delete-branch}.)"""
        assert _refuses(
            {"operation_type": "merge", "pr_number": "5", "bound_flags": ["--delete-branch"]},
            "gh pr merge 5 -dR owner/repo",
        )

    def test_mixed_cluster_Rd_value_consumption_refuses(self):
        """pflag semantics: in `-Rd owner/repo` the value-taking -R consumes the
        cluster remainder `d` as its value (→ --repo=d) and STOPS, so the approved
        {--delete-branch} never matches {--repo=d} → REFUSE. Pins that the cluster
        walk follows real pflag value-consumption, not naive per-char booleans."""
        assert _refuses(
            {"operation_type": "merge", "pr_number": "5", "bound_flags": ["--delete-branch"]},
            "gh pr merge 5 -Rd owner/repo",
        )

    def test_attached_cluster_dRvalue_adds_unapproved_repo_refuses(self):
        """`-dRowner/repo` (boolean -d then value-taking -R with attached value)
        binds {--delete-branch, --repo=owner/repo}; a flagless approval REFUSES —
        no bound short is dropped from the attached-value cluster form."""
        assert _refuses(
            {"operation_type": "merge", "pr_number": "5", "bound_flags": []},
            "gh pr merge 5 -dRowner/repo",
        )


class TestReadArmDroppedConstraintRefuses:
    """Set-equality is symmetric: an approval that carried a bound flag, executed
    WITHOUT it, also REFUSES (the dropped-constraint direction a subset rule would
    silently allow). Over-block-safe — the operator re-approves."""

    def test_approved_admin_executed_bare_refuses(self):
        assert _refuses(
            {"operation_type": "merge", "pr_number": "5", "bound_flags": ["--admin"]},
            "gh pr merge 5",
        )

    def test_approved_repo_executed_bare_refuses(self):
        assert _refuses(
            {"operation_type": "merge", "pr_number": "5", "bound_flags": ["--repo=owner/repo"]},
            "gh pr merge 5",
        )

    def test_approved_lease_executed_plain_refuses(self):
        """Approve a LEASE push to main, execute a PLAIN push → REFUSE — the
        dropped-flag direction of the plain↔lease token separation (#1064)."""
        assert _refuses(
            {"operation_type": "push-to-main", "target_ref": "main",
             "bound_flags": ["--force-with-lease"]},
            "git push origin main",
        )


class TestMatchHeadCommitDroppedConstraint:
    """--match-head-commit <sha> is a value-carrying SAFETY constraint (merge only
    if HEAD is still <sha>) — the canonical DROPPED-CONSTRAINT case set-equality
    catches that a subset rule (executed ⊆ approved) would silently ALLOW. Bound
    value-taking on the merge op-class (R4 denylist add).

    NON-VACUITY (essential): the approval token is SCANNER-DERIVED via
    extract_command_context, NOT hand-built. A hand-built
    {--match-head-commit=ABC123} token would REFUSE the bare execute REGARDLESS of
    whether --match-head-commit is in the denylist — the token/cmd asymmetry would
    drive the refusal, not the binding (vacuous w.r.t. the R4 config add). By
    deriving the token through the live scanner, a SOURCE-ONLY revert of the R4
    denylist add makes the scanner bind [] for --match-head-commit → the approval
    token's bound_flags collapse to {} → {} == {} → the dropped constraint
    AUTHORIZES → these REFUSE tests flip RED (the dropped-constraint, added, and
    different-value cases; the identical-match positive stays GREEN)."""

    _APPROVAL = "gh pr merge 5 --match-head-commit ABC123"

    @staticmethod
    def _scanner_token(approval_command: str) -> dict:
        """Build the approval token by SCANNING the approval command through the
        production SSOT, so bound_flags reflect the LIVE denylist (couples the
        dropped-constraint refusal to the --match-head-commit binding)."""
        from shared.merge_guard_common import extract_command_context

        return {"context": extract_command_context(approval_command)}

    def test_dropped_constraint_refuses(self):
        """Approve WITH the safety constraint, execute WITHOUT it → REFUSE — the
        dropped-constraint direction a subset rule would silently allow."""
        from merge_guard_pre import _token_matches_command

        assert not _token_matches_command(self._scanner_token(self._APPROVAL), "gh pr merge 5")

    def test_added_constraint_refuses(self):
        """Approve bare, execute WITH --match-head-commit → REFUSE (added direction;
        the executed side is scanned live)."""
        from merge_guard_pre import _token_matches_command

        assert not _token_matches_command(
            self._scanner_token("gh pr merge 5"), self._APPROVAL
        )

    def test_different_constraint_value_refuses(self):
        """Approve --match-head-commit ABC123, execute --match-head-commit DEF456 →
        REFUSE — a DIFFERENT head-sha is a different binding (the VALUE is bound,
        not merely the flag's presence)."""
        from merge_guard_pre import _token_matches_command

        assert not _token_matches_command(
            self._scanner_token(self._APPROVAL), "gh pr merge 5 --match-head-commit DEF456"
        )

    def test_identical_constraint_authorizes(self):
        """Approve and execute with the SAME constraint → AUTHORIZE — a faithful
        re-execution is not over-blocked."""
        from merge_guard_pre import _token_matches_command

        assert _token_matches_command(self._scanner_token(self._APPROVAL), self._APPROVAL)


# ════════════════════════════════════════════════════════════════════════════
# READ ARM — bounded over-block: an EXACT (form-invariant) match AUTHORIZES
# ════════════════════════════════════════════════════════════════════════════


class TestReadArmExactMatchAuthorizes:
    """Matching flags AUTHORIZE — the binding does not over-block a faithful
    re-execution, and it is FORM-INVARIANT (an approval in one CLI form authorizes
    an execution in any equivalent form, because both canonicalize identically)."""

    def test_admin_exact_match_authorizes(self):
        assert _authorizes(
            {"operation_type": "merge", "pr_number": "5", "bound_flags": ["--admin"]},
            "gh pr merge 5 --admin",
        )

    def test_clean_no_flag_match_authorizes(self):
        assert _authorizes(
            {"operation_type": "merge", "pr_number": "5", "bound_flags": []},
            "gh pr merge 5",
        )

    @pytest.mark.parametrize("command", [
        "gh --repo owner/repo pr merge 5",
        "gh pr merge 5 --repo=owner/repo",
        "gh pr merge 5 -R owner/repo",
        "gh pr merge 5 -Rowner/repo",
        "gh pr merge 5 -R=owner/repo",
    ], ids=["global_long", "equals_joined", "short_spaced", "short_attached",
            "short_equals_joined"])
    def test_repo_form_invariant_match_authorizes(self, command):
        """Approve the canonical --repo=owner/repo; every equivalent -R/--repo form
        of the same repo AUTHORIZES (all normalize to one token)."""
        assert _authorizes(
            {"operation_type": "merge", "pr_number": "5", "bound_flags": ["--repo=owner/repo"]},
            command,
        )

    @pytest.mark.parametrize("command", [
        "git push --no-verify origin main --force",
        "git push --no-verif origin main --force",
    ], ids=["exact", "git_abbreviation"])
    def test_no_verify_abbreviation_invariant_match_authorizes(self, command):
        """Approve --no-verify; both the exact form and the git abbreviation
        AUTHORIZE (the abbreviation expands to the same canonical token) — the
        expansion does not introduce a spurious over-block on a faithful re-run."""
        assert _authorizes(
            {"operation_type": "force-push", "target_ref": "main", "bound_flags": ["--no-verify"]},
            command,
        )

    @pytest.mark.parametrize("command", [
        "gh pr merge 5 -d",
        "gh pr merge 5 --delete-branch",
    ], ids=["short", "long"])
    def test_delete_branch_form_invariant_match_authorizes(self, command):
        assert _authorizes(
            {"operation_type": "merge", "pr_number": "5", "bound_flags": ["--delete-branch"]},
            command,
        )

    def test_admin_plus_repo_combined_match_authorizes(self):
        """Two bound flags approved and executed (order/placement-invariant) →
        AUTHORIZE."""
        assert _authorizes(
            {"operation_type": "merge", "pr_number": "5",
             "bound_flags": ["--admin", "--repo=owner/repo"]},
            "gh --repo owner/repo pr merge 5 --admin",
        )

    def test_lease_exact_match_authorizes(self):
        """Approve and execute the SAME lease push → AUTHORIZE — the presence bind
        must not over-block the faithful lease click the #1064 fix un-blocks."""
        assert _authorizes(
            {"operation_type": "push-to-main", "target_ref": "main",
             "bound_flags": ["--force-with-lease"]},
            "git push --force-with-lease origin main",
        )


# ════════════════════════════════════════════════════════════════════════════
# SCANNER — extract_privileged_flags: CLI-form canonicalization output pins
# ════════════════════════════════════════════════════════════════════════════


class TestScannerCanonicalForms:
    """Direct output pins for the scanner's form normalization (the C1 contract).
    Every CLI form of a bound flag canonicalizes to ONE token so the read-side
    set-equality is form-invariant."""

    _FORMS = [
        # --admin (boolean)
        ("gh pr merge 5 --admin", "merge", ["--admin"]),
        # -R/--repo (value-taking) — all forms → --repo=owner/repo
        ("gh pr merge 5 -R owner/repo", "merge", ["--repo=owner/repo"]),
        ("gh --repo owner/repo pr merge 5", "merge", ["--repo=owner/repo"]),
        ("gh pr merge 5 --repo=owner/repo", "merge", ["--repo=owner/repo"]),
        ("gh pr merge 5 -Rowner/repo", "merge", ["--repo=owner/repo"]),
        # short =-joined (-R=value): exercises the cluster-path `=`-strip, a
        # DISTINCT branch from the long --repo=value form above.
        ("gh pr merge 5 -R=owner/repo", "merge", ["--repo=owner/repo"]),
        # combined-short cluster: -d boolean kept, -R consumes the next token
        ("gh pr merge 5 -dR owner/repo", "merge", ["--delete-branch", "--repo=owner/repo"]),
        # combined-short cluster, attached value: -d boolean, -R consumes "owner/repo"
        ("gh pr merge 5 -dRowner/repo", "merge", ["--delete-branch", "--repo=owner/repo"]),
        # pflag value-consumption: -Rd → -R takes the cluster remainder "d" and STOPS
        ("gh pr merge 5 -Rd owner/repo", "merge", ["--repo=d"]),
        # cluster with an UNBOUND short (-s ignored), bound -d kept
        ("gh pr merge 5 -sd", "merge", ["--delete-branch"]),
        ("gh pr merge 5 -d", "merge", ["--delete-branch"]),
        # --match-head-commit (value-taking SAFETY constraint, R4): spaced + =-joined
        ("gh pr merge 5 --match-head-commit ABC123", "merge", ["--match-head-commit=ABC123"]),
        ("gh pr merge 5 --match-head-commit=ABC123", "merge", ["--match-head-commit=ABC123"]),
        # benign: no bound flag → empty set
        ("gh pr merge 5", "merge", []),
        # close op-class: -R bound; --delete-branch/-d now BOUND (GAP2 — bare close
        # would otherwise set-equal the delete variant → escalation).
        ("gh pr close 5 -R x/y", "close", ["--repo=x/y"]),
        ("gh pr close 5 -R=x/y", "close", ["--repo=x/y"]),  # short =-joined on close
        ("gh pr close 5 --delete-branch", "close", ["--delete-branch"]),
        # force-push: --no-verify (exact)
        ("git push --no-verify origin main --force", "force-push", ["--no-verify"]),
        # push-to-main: --force-with-lease presence bind (#1064). BOOLEAN — the
        # =-joined <ref>:<expect> value is dropped, so every lease spelling binds
        # ONE canonical bare token (intra-lease value variation is an accepted
        # residual; see TestLeaseNegationResidualTripwire).
        ("git push --force-with-lease origin main", "push-to-main", ["--force-with-lease"]),
        ("git push --force-with-lease=main:abc123 origin main", "push-to-main", ["--force-with-lease"]),
        ("git push origin main", "push-to-main", []),
    ]

    @pytest.mark.parametrize(
        "command,op,expected", _FORMS,
        ids=[f"{op}:{cmd}" for (cmd, op, expected) in _FORMS],
    )
    def test_canonical_output(self, command, op, expected):
        from shared.merge_guard_common import extract_privileged_flags

        assert extract_privileged_flags(command, op) == expected

    @pytest.mark.parametrize("op", [None, "api", "branch-delete"])
    def test_unflagged_op_classes_bind_empty_set(self, op):
        """None / API / the (today) flagless branch-delete class yield [] — the
        over-block-safe default for op-classes with no denylist entry."""
        from shared.merge_guard_common import extract_privileged_flags

        assert extract_privileged_flags("git push --no-verify origin main", op) == []


class TestGitGhAbbreviationAsymmetry:
    """The is_git_surface branch is SECURITY-LOAD-BEARING (architect §7.1): git
    EXPANDS unambiguous long-prefix abbreviations (a miss under-blocks), gh REJECTS
    them (the command errors out, so a miss cannot bypass). The scanner expands on
    the git surface ONLY."""

    @pytest.mark.parametrize("command", [
        "git push --no-verif origin main --force",
        "git push --no-ver origin main --force",
        "git push --no-v origin main --force",
        "git push --no origin main --force",
    ], ids=["no-verif", "no-ver", "no-v", "no"])
    def test_git_surface_expands_unambiguous_prefix(self, command):
        from shared.merge_guard_common import extract_privileged_flags

        assert extract_privileged_flags(command, "force-push") == ["--no-verify"]

    def test_gh_surface_does_not_expand_abbreviation(self):
        """gh rejects abbreviation, so `--admi` is NOT expanded to --admin on the
        merge (gh) surface → [] (no spurious over-block; gh would error the command
        anyway). The contrast with the git case above pins the asymmetry."""
        from shared.merge_guard_common import extract_privileged_flags

        assert extract_privileged_flags("gh pr merge 5 --admi", "merge") == []
        # Control: the SAME logical intent on the git surface DOES expand.
        assert extract_privileged_flags(
            "git push --no-verif origin main", "force-push"
        ) == ["--no-verify"]

    def test_push_to_main_git_surface_expands_lease_abbreviation(self):
        """push-to-main joined the git-surface set with the lease bind (#1064) as
        DEFENSE-IN-DEPTH: given op_type=push-to-main, git's abbreviation rule says
        a truncated `--force-with-leas` must bind the canonical token, and this
        pins that the expansion does so. The case is NOT live-reachable today:
        any abbreviation still containing `--force` classifies FORCE-PUSH first
        (the force arms' lookahead excludes only the exact `-with-lease` suffix),
        and the shorter prefixes that do classify push-to-main (`--forc`, `--fo`)
        are git-ambiguous — git rejects the command, so no live lease push runs
        unbound. The expansion keeps the bind correct if either neighbor shifts."""
        from shared.merge_guard_common import extract_privileged_flags

        assert extract_privileged_flags(
            "git push --force-with-leas origin main", "push-to-main"
        ) == ["--force-with-lease"]


class TestLeaseNegationResidualTripwire:
    """Documented-behavior pin for an ACCEPTED residual (#1064 design): a boolean
    bind treats an explicit `=false|0|no` as flag-DISABLED (the safe form, shared
    with --admin=false), so `--force-with-lease=false` binds NOTHING — yet git
    reads `false` as a REFNAME, making the command a live lease push that a
    plain-push token would authorize. Accepted because the negation values are
    implausible branch names in honest use, the executed command is still
    lease-protected (never plain-force), the guard's charter is honest mistakes
    (not adversarial value-crafting), and the same corner exists by design for
    EVERY boolean privileged flag. Do NOT "fix" this by marking the flag
    value-taking — that would consume the next positional on the bare spelling
    and import mint-side adjacency-sensitivity (an over-block risk, the cardinal
    sin); if this pin flips, re-read the residual's disposition first."""

    def test_negated_lease_value_binds_nothing(self):
        from shared.merge_guard_common import extract_privileged_flags

        assert extract_privileged_flags(
            "git push --force-with-lease=false origin main", "push-to-main"
        ) == []


class TestEndOfOptionsMarkerOverBlock:
    """Documented OVER-BLOCK edge: the scanner does NOT treat `--` (end-of-options)
    as a terminator, so a privileged flag positioned AFTER `--` is still bound —
    even though gh/git would treat post-`--` tokens as positionals (and would
    likely not honor --admin as a flag at all). This is over-block-SAFE and
    arm-SYMMETRIC: both arms call the same SSOT, so an identical approve/execute
    pair still MATCHES → AUTHORIZE; the binding only ever over-refuses a DIVERGENT
    pair, never under-blocks. Pinned so a future `--`-handling change cannot
    silently flip it into an UNDER-block (binding [] past `--` would let
    `approve bare / execute -- --admin` AUTHORIZE)."""

    def test_scanner_does_not_terminate_on_double_dash(self):
        """Arm-symmetric by construction: the shared SSOT binds --admin past `--`,
        so BOTH arms classify it identically (no per-arm `--` divergence)."""
        from shared.merge_guard_common import extract_privileged_flags

        assert extract_privileged_flags("gh pr merge 5 -- --admin", "merge") == ["--admin"]

    def test_symmetric_double_dash_pair_authorizes(self):
        """approve `... -- --admin` / execute the SAME → AUTHORIZE: both arms bind
        --admin, so the over-block does not perturb a faithful re-execution."""
        assert _authorizes(
            {"operation_type": "merge", "pr_number": "5", "bound_flags": ["--admin"]},
            "gh pr merge 5 -- --admin",
        )

    def test_divergent_double_dash_pair_refuses_as_over_block(self):
        """approve bare / execute `... -- --admin` → REFUSE: the executed --admin
        (still bound past `--`) was never approved. Over-block-safe — re-approval
        recovers; this is never an under-block."""
        assert _refuses(
            {"operation_type": "merge", "pr_number": "5", "bound_flags": []},
            "gh pr merge 5 -- --admin",
        )


# ════════════════════════════════════════════════════════════════════════════
# MULTIPLICITY — bound_flags is a pair ATTRIBUTE, not (op,target) identity
# ════════════════════════════════════════════════════════════════════════════


class TestFlagVariationIsPairAttribute:
    """Flag variation must NOT change the distinct-(op,target) count: bound_flags
    is excluded from pair identity, so a bundle whose surfaces differ only by a
    privileged flag stays ONE pair and cannot spuriously trip the SACROSANCT
    multiplicity (>1 distinct pair) refusal."""

    def test_flag_variation_does_not_inflate_pair_count(self):
        from merge_guard_post import _collect_pairs

        pairs = _collect_pairs(["Approve: gh pr merge 5", "Execute: gh pr merge 5 --admin"])
        assert len(pairs) == 1
        assert ("merge", "5") in pairs

    def test_many_flag_forms_same_pr_stay_one_pair(self):
        from merge_guard_post import _collect_pairs

        pairs = _collect_pairs([
            "gh pr merge 5 --admin",
            "gh pr merge 5 -R owner/repo",
            "gh pr merge 5 -d",
        ])
        assert len(pairs) == 1
        assert ("merge", "5") in pairs

    def test_different_pr_still_two_pairs_control(self):
        """Control: a genuine target divergence (different PR) DOES count as two
        distinct pairs — so the single-pair result above is the flag-attribute
        property, not a degenerate collapse of everything to one pair."""
        from merge_guard_post import _collect_pairs

        pairs = _collect_pairs(["gh pr merge 5 --admin", "gh pr merge 6"])
        assert len(pairs) == 2
