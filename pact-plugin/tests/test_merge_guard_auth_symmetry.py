"""Bidirectional merge-guard auth-token symmetry suite (mint-vs-read).

Proves the command-anchored mint-vs-read fix in BOTH directions and is
demonstrably NON-VACUOUS on a SACROSANCT security control:

  must-NOT-authorize  (the #1032 false-AUTHORIZE bypasses):  A1 A2 A3 A4 A-DEFER
                      + the malformed-context read fail-open
  must-AUTHORIZE      (the #1031 false-REJECT regressions):  R1..R7

Security model (architect blueprint §5, converged contract): the on-disk token
is the post(mint)→pre(read) seam; BOTH arms classify a COMMAND STRING via the
SAME shared SSOT (extract_command_context), so they cannot drift. The read side
authorizes ONLY when token and command agree on op-type AND that op's target
(fail-closed: any axis absent/mismatched → REFUSE, no terminal allow). The mint
side reads the SELECTED option's command (never prose), vetoes decline/defer
FIRST, and mints exactly ONE distinct (op,target) pair.

NON-VACUITY discipline (#933 — the crux). Two classes, two proof techniques.
The counter-test cardinalities below are verified by SOURCE-ONLY revert of the
fix commits (testing-strategies "Counter-test-by-revert"): restore ONE hook
source file to its pre-fix shape, leave this test file in place, re-run; the
named test(s) must flip RED. Drive points are chosen so the revert changes
BEHAVIOR rather than ImportError-ing on a net-new symbol — the bypass tests go
through the STABLE entry points (post.main() / check_merge_authorization), never
the net-new _mint_context_from_bundle directly.

  CLASS-I  (authorized/mis-minted at HEAD; a source-only revert flips RED):
    * read-floor cases  → revert C2 (merge_guard_pre.py read predicate)
    * mint cases        → revert C3 (merge_guard_post.py mint rewire)
  CLASS-II (already-true at baseline; a revert CANNOT flip it — needs an
            ADD-mutation): S-ADV non-selected-option smuggle, decline-label
            guardrail. Each carries an ADD-mutation proof in its docstring.

Counter-test commits (worktree fix/merge-guard-auth-symmetry):
    C1 = b96ae745  shared SSOT extractor (relocation, behavior-neutral)
    C2 = f3db9f9c  merge_guard_pre fails closed   (C2^ = C1 = b96ae745)
    C3 = 5e3d2436  merge_guard_post mint rewire    (C3^ = C2 = f3db9f9c)

Test levels (architect §7):
    L-A  function-level real seam — extract_command_context → write_token(tmp)
         → check_merge_authorization(tmp); only token_dir injected (a PRODUCTION
         param, NOT a mock — no seam is stubbed).
    L-B  in-process main() via patched TOKEN_DIR + sys.stdin (subprocess count
         = 0); covers the JSON-envelope + gate boundary L-A cannot see.
"""

from __future__ import annotations

import io
import json
import time
from unittest.mock import patch

import pytest

from merge_guard_post import main as _post_main
from merge_guard_pre import check_merge_authorization, main as _pre_main
from shared.merge_guard_common import TOKEN_PREFIX, extract_command_context

# ───────────────────────────── helpers (no mocks of the seam) ────────────────

_seed_counter = 0


def _q(question: str, options: list | None = None, multiSelect: bool = False) -> dict:
    """Build one AskUserQuestion question dict. options=None → free-text arm."""
    q: dict = {"question": question, "multiSelect": multiSelect}
    if options is not None:
        q["options"] = options
    return q


def _opt(label: str, description: str = "") -> dict:
    return {"label": label, "description": description}


def _minted_tokens(tmp) -> list:
    """Live (un-consumed) minted token files in tmp — excludes .consumed and
    per-use .use-N markers so a count reflects MINTS, not lifecycle artifacts."""
    return sorted(
        p for p in tmp.glob(f"{TOKEN_PREFIX}*")
        if not p.name.endswith(".consumed") and ".use-" not in p.name
    )


def _invoke_post(questions: list, answers: dict, tmp) -> int:
    """L-B: drive merge_guard_post.main() in-process over a real stdin envelope
    with TOKEN_DIR redirected to tmp. Returns the process exit code. Mint runs
    through the STABLE entry point so a C3 source-only revert changes behavior."""
    envelope = json.dumps({
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": questions},
        "tool_response": {"answers": answers},
        "session_id": "test-auth-symmetry",
    })
    with patch("merge_guard_post.TOKEN_DIR", tmp), \
         patch("sys.stdin", io.StringIO(envelope)):
        with pytest.raises(SystemExit) as exc:
            _post_main()
    return exc.value.code


def _invoke_pre(command: str, tmp) -> tuple[int, str]:
    """L-B: drive merge_guard_pre.main() in-process. Returns (exit_code, stdout)."""
    envelope = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "test-auth-symmetry",
    })
    out = io.StringIO()
    with patch("merge_guard_pre.TOKEN_DIR", tmp), \
         patch("sys.stdin", io.StringIO(envelope)), \
         patch("sys.stdout", out):
        with pytest.raises(SystemExit) as exc:
            _pre_main()
    return exc.value.code, out.getvalue()


def _authorize(command: str, tmp) -> str | None:
    """L-A: the read side over the real token seam. None = allowed, str = denied
    reason. check_merge_authorization is stable across a C2 source-only revert
    (only its inner _token_matches_command body changed), so read-floor
    counter-tests drive it directly."""
    return check_merge_authorization(command, token_dir=tmp)


def _seed_token(tmp, context, expires_in: int = 300):
    """Write a token file to disk DIRECTLY (bypassing write_token's fail-closed
    write gate). Required for the read-floor counter-tests: the new write_token
    REFUSES untyped / malformed-context tokens, so the only way to exercise the
    read predicate against such a token is to forge it on disk — exactly the
    hand-crafted-token threat the read floor must withstand. No session_id →
    no cross-session scoping check (graceful degradation)."""
    global _seed_counter
    _seed_counter += 1
    now = time.time()
    data = {
        "created_at": now,
        "expires_at": now + expires_in,
        "context": context,
        "max_uses": 2,
        "uses_remaining": 2,
    }
    path = tmp / f"{TOKEN_PREFIX}{int(now)}-{_seed_counter:04d}"
    path.write_text(json.dumps(data))
    return path


# ════════════════════════════════════════════════════════════════════════════
# #1032 — must-NOT-authorize (the false-AUTHORIZE bypasses)
# ════════════════════════════════════════════════════════════════════════════

class TestBypassA1ForcePushUntyped:
    """A1 — force-push to main mis-authorized by an UNTYPED token minted from
    'merged' prose. CLASS-I, DEFENSE-IN-DEPTH: closed by BOTH the read floor
    (op_type=None → deny) AND the command-driven mint (no embedded command →
    no token). Reverting ONE fix stays green because the other still blocks, so
    the two mechanisms are asserted SEPARATELY (architect §7)."""

    def test_read_floor_denies_untyped_token(self, tmp_path):
        """MECHANISM 1 (read floor). A forged untyped token cannot authorize a
        force-push. Counter-test: source-only revert C2 (merge_guard_pre.py) →
        the old terminal `return True` fall-through authorizes → {1 fail}."""
        _seed_token(tmp_path, {"operation_type": None, "branch": "deploy"})
        assert _authorize("git push --force origin main", tmp_path) is not None

    def test_mint_writes_no_token_for_prose_only_bundle(self, tmp_path):
        """MECHANISM 2 (command-driven mint). The A1 question carries NO embedded
        command (only the word 'merged' + a branch name in prose), so the mint
        finds zero (op,target) pairs and writes NO token. Counter-test:
        source-only revert C3 (merge_guard_post.py) → the old prose-extractor
        mints an untyped {branch:'deploy'} token → a token file appears →
        {1 fail}."""
        code = _invoke_post(
            [_q("Has the merged feature branch deploy been verified?",
                [_opt("Yes", "Confirmed deployed")])],
            {"Has the merged feature branch deploy been verified?": "Yes"},
            tmp_path,
        )
        assert code == 0
        assert _minted_tokens(tmp_path) == []

    def test_endtoend_force_push_to_main_blocked(self, tmp_path):
        """End-to-end: the A1 approval bundle does not authorize the force-push."""
        _invoke_post(
            [_q("Has the merged feature branch deploy been verified?",
                [_opt("Yes", "Confirmed deployed")])],
            {"Has the merged feature branch deploy been verified?": "Yes"},
            tmp_path,
        )
        code, out = _invoke_pre("git push --force origin main", tmp_path)
        assert code == 2
        assert '"permissionDecision": "deny"' in out


class TestBypassA2NowMergedPr:
    """A2 — 'now-merged PR 1234' mints an untyped {pr:1234, op:None} token (the
    bare 'merge' gate fired on 'merged' but \\bmerge\\b missed it for op
    classification). CLASS-I, DEFENSE-IN-DEPTH (same split as A1)."""

    def test_read_floor_denies_untyped_pr_token(self, tmp_path):
        """MECHANISM 1 (read floor): an untyped token with a coincidental
        pr_number cannot authorize `gh pr merge 1234`. Counter-test: revert C2 →
        old code skips the typed-guard for op=None then matches pr 1234==1234 →
        returns True → {1 fail}."""
        _seed_token(tmp_path, {"operation_type": None, "pr_number": "1234"})
        assert _authorize("gh pr merge 1234", tmp_path) is not None

    def test_mint_writes_no_token_for_prose_pr(self, tmp_path):
        """MECHANISM 2 (mint): 'PR 1234' in prose is not a command region → no
        token. Counter-test: revert C3 → old extractor mints {pr:1234} → {1 fail}."""
        code = _invoke_post(
            [_q("Approve the now-merged PR 1234 for the changelog?",
                [_opt("Yes", "Add to changelog")])],
            {"Approve the now-merged PR 1234 for the changelog?": "Yes"},
            tmp_path,
        )
        assert code == 0
        assert _minted_tokens(tmp_path) == []


class TestBypassA3A4ProseCoincidentalPr:
    """A3/A4 — the OLD mint extracted a PR number from QUESTION PROSE (an issue
    ref '#9999'; 'PR 2 days' → 2), so the token authorized a command the operator
    never approved. CLASS-I, SINGLE-MECHANISM: the read floor does NOT close
    these (op=merge matches, pr coincides) — ONLY the command-anchored mint
    closes them, by extracting the REAL pr from the SELECTED option's command.
    End-to-end revert of C3 is therefore sound."""

    def test_a3_binds_command_pr_not_issue_distractor(self, tmp_path):
        """A3: question prose mentions issue #9999; the selected option carries
        the real `gh pr merge 42`. Mint binds pr=42, NOT 9999. Counter-test:
        revert C3 → old mint reads the question's '#9999' → token pr=9999 →
        `gh pr merge 9999` is AUTHORIZED → the deny assertion below flips RED
        ({≥1 fail})."""
        q = "Approve the hotfix merge? Tracking issue #9999 has the context."
        self._mint_a3a4(tmp_path, q, "Yes, merge", "Run `gh pr merge 42`")
        # The approved command authorizes.
        assert _authorize("gh pr merge 42", tmp_path) is None

    def test_a3_distractor_command_denied(self, tmp_path):
        q = "Approve the hotfix merge? Tracking issue #9999 has the context."
        self._mint_a3a4(tmp_path, q, "Yes, merge", "Run `gh pr merge 42`")
        assert _authorize("gh pr merge 9999", tmp_path) is not None

    def test_a4_pr_two_days_distractor_denied(self, tmp_path):
        """A4: 'PR 2 days ago' → the old extractor grabbed 2. The option carries
        the real `gh pr merge 1029`. `gh pr merge 2` must be denied; the real
        `gh pr merge 1029` is authorized. (A mismatched command is NOT consumed,
        so the distractor-deny leaves the token intact for the real authorize.)
        Counter-test: revert C3 → old mint binds pr=2 from prose → `gh pr merge
        2` is AUTHORIZED → the deny assertion flips RED."""
        q = "Merge the branch we discussed re: the PR 2 days ago?"
        self._mint_a3a4(tmp_path, q, "Yes, merge", "Run `gh pr merge 1029`")
        assert _authorize("gh pr merge 2", tmp_path) is not None
        assert _authorize("gh pr merge 1029", tmp_path) is None

    @staticmethod
    def _mint_a3a4(tmp_path, question, label, description):
        code = _invoke_post(
            [_q(question, [_opt(label, description), _opt("Cancel", "Abort")])],
            {question: label},
            tmp_path,
        )
        assert code == 0


class TestBypassADeferVetoPrecedence:
    """A-DEFER — the 5th #1032 bypass (question-side, cleanest): a merge question
    embedding the command, answered with a DEFERRAL ('Approve later' / 'Yes, but
    review first' / 'Sure, once tests pass') → the old is_affirmative('^Yes...')
    fired → token MINTED → `gh pr merge N` authorized. Closed by the decline/
    defer GLOBAL veto that runs FIRST, with precedence over command presence.
    CLASS-I: revert C3 (which restores the affirmative-prefix mint without the
    veto) → re-RED."""

    @pytest.mark.parametrize("deferral", [
        "Yes, but review first",
        "Approve later",
        "Sure, once tests pass",
        "Yes — after I check CI",
    ])
    def test_free_text_deferral_does_not_mint(self, tmp_path, deferral):
        """Free-text arm: a deferral answer to a command-embedding question mints
        nothing. Counter-test: revert C3 → old is_affirmative prefix-matches
        'Yes'/'Approve'/'Sure' → token minted → {≥1 fail}."""
        q = "Merge the release? Run `gh pr merge 42` when ready."
        code = _invoke_post([_q(q)], {q: deferral}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []
        assert _authorize("gh pr merge 42", tmp_path) is not None


class TestReadMalformedContextFailsClosed:
    """The malformed-context read fail-open (F-READ-1): a forged token whose
    `context` is not a dict proved nothing yet the old read returned True.
    CLASS-I (read-floor): revert C2 → re-RED."""

    def test_non_dict_context_denies(self, tmp_path):
        """Counter-test: revert C2 → `if not isinstance(context, dict): return
        True` authorizes → {1 fail}."""
        _seed_token(tmp_path, "this-is-not-a-dict")
        assert _authorize("gh pr merge 42", tmp_path) is not None

    def test_missing_context_key_denies(self, tmp_path):
        global _seed_counter
        _seed_counter += 1
        now = time.time()
        # A token with NO context key at all (forged shape).
        path = tmp_path / f"{TOKEN_PREFIX}{int(now)}-nc-{_seed_counter:04d}"
        path.write_text(json.dumps({
            "created_at": now, "expires_at": now + 300,
            "max_uses": 2, "uses_remaining": 2,
        }))
        assert _authorize("gh pr merge 42", tmp_path) is not None


# ════════════════════════════════════════════════════════════════════════════
# #1031 — must-AUTHORIZE (the false-REJECT regressions). Each authorizes at HEAD
# and (except R2, the no-regression control) is RED under a C3 source-only
# revert — naturally non-vacuous: the OLD prose-extractor mis-binds the target.
# ════════════════════════════════════════════════════════════════════════════

class TestRegressionMustAuthorize:
    def _approve(self, tmp_path, question, label, description):
        code = _invoke_post(
            [_q(question, [_opt(label, description), _opt("Cancel", "Abort")])],
            {question: label},
            tmp_path,
        )
        assert code == 0

    def test_r1_umbrella_subject_distractor(self, tmp_path):
        """R1: an umbrella '#1028' distractor in prose; the real merge is 1029.
        Command-anchored mint binds 1029 → authorized."""
        q = "Merge the umbrella PR? (rolls up #1028 work)"
        self._approve(tmp_path, q, "Yes, merge", "Run `gh pr merge 1029`")
        assert _authorize("gh pr merge 1029", tmp_path) is None

    def test_r2_control_no_distractor(self, tmp_path):
        """R2: NO-REGRESSION CONTROL — a clean approval whose prose pr already
        equals the command pr. Authorizes at HEAD AND under a C3 revert (stays
        green when reverted — that is its job: it must not be coupled to the
        fix)."""
        q = "Merge PR 1029?"
        self._approve(tmp_path, q, "Yes, merge", "Run `gh pr merge 1029`")
        assert _authorize("gh pr merge 1029", tmp_path) is None

    def test_r3_per_issue_distractor(self, tmp_path):
        q = "Per issue #1028, merge the follow-up PR?"
        self._approve(tmp_path, q, "Yes, merge", "Run `gh pr merge 1029`")
        assert _authorize("gh pr merge 1029", tmp_path) is None

    def test_r4_lands_fix_from_issue(self, tmp_path):
        q = "This lands the fix from issue #1028 — merge it?"
        self._approve(tmp_path, q, "Yes, merge", "Run `gh pr merge 1029`")
        assert _authorize("gh pr merge 1029", tmp_path) is None

    def test_r5_branch_axis(self, tmp_path):
        """R5: branch-delete target axis. Command-anchored branch name binds."""
        q = "Delete the merged feature branch?"
        self._approve(tmp_path, q, "Yes, delete", "Run `git branch -D feature/login`")
        assert _authorize("git branch -D feature/login", tmp_path) is None

    def test_r6_op_type_axis(self, tmp_path):
        """R6: op-type axis. Prose mentions 'force-push' but the approved command
        is a branch-delete; mint binds the COMMAND's op, not the prose word."""
        q = "We discussed a force-push earlier — delete the stale branch instead?"
        self._approve(tmp_path, q, "Yes, delete", "Run `git branch -D stale/x`")
        assert _authorize("git branch -D stale/x", tmp_path) is None

    def test_r7_command_only_in_option_descriptive_label(self, tmp_path):
        """R7 / #1034: the command lives ONLY in the option (label 'Merge now',
        not in the old affirmative allowlist). Option-mode exact-label match +
        command-anchored mint → pr 1034 authorized.

        PROVISIONAL: reconstructed from the documented payload structure (the
        verbatim #1034 capture was not preserved). Asserts mint==1034 from the
        selected-option command, which is the load-bearing #1034 property."""
        q = ("We have reviewed the changes thoroughly across several files and "
             "the CI is green; the team has signed off on the approach.")
        self._approve(tmp_path, q, "Merge now", "Run `gh pr merge 1034`")
        assert _authorize("gh pr merge 1034", tmp_path) is None


# ════════════════════════════════════════════════════════════════════════════
# WGE-1..5 — write-gate-evasion suite (architect §7)
# ════════════════════════════════════════════════════════════════════════════

class TestWriteGateEvasion:
    # (Floor reconcile) WGE-1 (decline-label 'Continue reviewing' carrying the
    # command) and WGE-2 (defer-label 'Approve after CI' carrying the command)
    # asserted the now-REMOVED decline/defer veto. Under the minimal floor those
    # selections MINT (the operator clicked a command-bearing option) — a documented
    # ACCEPTED RESIDUAL (#21). Their residual coverage moved to
    # TestDeclineLabelCarryingCommandResidualMints below; deleted here because the
    # veto mechanism they exercised no longer exists.

    def test_wge3_answer_matching_no_label_no_iter_fallback(self, tmp_path):
        """WGE-3: an answer matching NO option label refuses (the KD-12 fix kills
        the next(iter(answers)) fallback — a populated non-matching answer never
        binds some other option)."""
        q = "Merge?"
        opts = [_opt("Yes, merge", "Run `gh pr merge 42`"), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts)], {q: "Maybe"}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []
        assert _authorize("gh pr merge 42", tmp_path) is not None

    def test_wge4_multiselect_conflicting_refused_as_mint_source(self, tmp_path):
        """WGE-4: a multiSelect question is refused as a mint source (v1
        over-block), even with conflicting command-bearing options."""
        q = "Pick the ops to run:"
        opts = [_opt("Merge 42", "Run `gh pr merge 42`"),
                _opt("Merge 99", "Run `gh pr merge 99`")]
        code = _invoke_post([_q(q, opts, multiSelect=True)],
                            {q: "Merge 42, Merge 99"}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []
        assert _authorize("gh pr merge 42", tmp_path) is not None
        assert _authorize("gh pr merge 99", tmp_path) is not None

    def test_wge5_descriptive_is_merge_question_overfire_harmless(self, tmp_path):
        """WGE-5: is_merge_question over-firing (a command-bearing question) is
        harmless under the command-driven model — without a valid approval no
        token mints. Here the answer matches no label → no approval → no mint."""
        from merge_guard_post import is_merge_question
        q = "FYI this will eventually run `gh pr merge 42` — anything to flag?"
        assert is_merge_question(q) is True   # the coarse hint over-fires...
        opts = [_opt("Looks fine", "No concerns"), _opt("Hold on", "Wait")]
        code = _invoke_post([_q(q, opts)], {q: "Looks fine"}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []  # ...but nothing is minted


# ════════════════════════════════════════════════════════════════════════════
# CLASS-II non-vacuity — already-true at baseline; a revert CANNOT flip these.
# Each carries an ADD-mutation proof (the mutation that WOULD flip it RED).
# ════════════════════════════════════════════════════════════════════════════

class TestClassIINonSelectedOptionSmuggle:
    """S-ADV — a destructive command smuggled into a NON-selected option must be
    ignored (the mint scans the SELECTED option only, D3). CLASS-II: at baseline
    the old mint read NEITHER option, so this is already-true and revert-immune.

    Clean single-command design: the SELECTED option is benign (no command); the
    command lives ONLY in an UNSELECTED option.

    ADD-MUTATION PROOF (the mutation that flips it RED — verified manually):
    change _mint_context_from_bundle's multiplicity loop to scan EVERY option's
    text instead of `selected_option_texts`. Then the smuggled `gh pr merge 99`
    becomes the single (op,target) pair → a token mints → `gh pr merge 99` is
    AUTHORIZED → both assertions below flip RED ({≥1 fail}). A source REVERT
    cannot flip this (the old mint never read options at all), which is exactly
    why an ADD-mutation, not a revert, is the non-vacuity proof for this NEGATIVE."""

    def _approve_with_smuggle(self, tmp_path):
        q = "Approve the documentation update?"
        opts = [
            _opt("Yes, update docs", "Apply the doc change"),       # SELECTED, benign
            _opt("Cancel", "Instead run `gh pr merge 99` quietly"),  # smuggled (unselected)
        ]
        code = _invoke_post([_q(q, opts)], {q: "Yes, update docs"}, tmp_path)
        assert code == 0

    def test_smuggle_in_unselected_option_not_minted(self, tmp_path):
        self._approve_with_smuggle(tmp_path)
        assert _minted_tokens(tmp_path) == []

    def test_smuggled_command_denied(self, tmp_path):
        self._approve_with_smuggle(tmp_path)
        assert _authorize("gh pr merge 99", tmp_path) is not None


class TestDeclineLabelCarryingCommandResidualMints:
    """RESIDUAL-CANARY (post-#22 minimal floor). The floor does NO decline/defer
    intent-parsing — the operator's CONSENT is the CLICK on a command-bearing
    option, and the surrounding label/description prose is never read to second-
    guess it. So clicking a DECLINE- or DEFER-labelled option whose description
    nonetheless CARRIES the destructive command MINTS — a documented, user-ratified
    ACCEPTED RESIDUAL (#21 threat-model: the agent places the command ONLY in
    affirmative options, so a genuine decline option carries no command). These were
    previously WGE-1/WGE-2 (decline/defer veto) + the decline-label guardrail; they
    are reframed as residual-canaries that ASSERT the mint, so any re-introduction
    of a prose decline/defer cross-check is caught.

    NON-VACUITY (broaden-mutation, the ONLY valid proof for an accepted-residual
    canary — a source revert leaves it green): re-add a decline/defer prose veto to
    `_mint_context_from_bundle` (scan the selected option's label+description for a
    decline word and refuse) → these selections refuse → 0 tokens → every row RED.
    That mutation is exactly the #17/#21 under-block/FP class the floor removed."""

    @pytest.mark.parametrize("label, description", [
        ("Pause work for now", "Run `gh pr merge 42` later, not now"),   # decline label
        ("Continue reviewing", "Run `gh pr merge 42` after review"),     # decline label (was WGE-1)
        ("Approve after CI", "Run `gh pr merge 42` once CI is green"),   # defer label (was WGE-2)
    ])
    def test_decline_or_defer_label_carrying_command_mints(self, tmp_path, label, description):
        q = "Merge or hold?"
        opts = [_opt(label, description), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts)], {q: label}, tmp_path)
        assert code == 0
        assert len(_minted_tokens(tmp_path)) == 1, (
            f"floor residual: clicking decline/defer label {label!r} carrying the "
            "command must MINT (no prose intent-parsing)"
        )
        assert _authorize("gh pr merge 42", tmp_path) is None


class TestA5NoTokenNonCase:
    """A5 — the refuted non-case (documented for completeness, not a live bypass):
    with NO token on disk, the read side denies every destructive command. This
    is the fail-closed default; it holds at baseline and post-fix alike."""

    def test_no_token_denies_all(self, tmp_path):
        assert _authorize("gh pr merge 42", tmp_path) is not None
        assert _authorize("git push --force origin main", tmp_path) is not None
        assert _authorize("git branch -D feature/x", tmp_path) is not None


# ════════════════════════════════════════════════════════════════════════════
# Anti-drift / SSOT: both arms classify the SAME command identically
# ════════════════════════════════════════════════════════════════════════════

class TestMintReadParity:
    """#720 dual-hook parity: a token minted from a command authorizes exactly
    that command and denies a different one — proving mint and read derive
    (op,target) from the SAME shared extractor (no asymmetric drift)."""

    @pytest.mark.parametrize("command,other", [
        ("gh pr merge 42", "gh pr merge 43"),
        ("git branch -D feature/x", "git branch -D feature/y"),
    ])
    def test_minted_command_round_trips(self, tmp_path, command, other):
        ctx = extract_command_context(command)
        from merge_guard_post import write_token
        token_path = write_token(ctx, token_dir=tmp_path)
        assert token_path is not None
        assert _authorize(command, tmp_path) is None        # exact match authorizes
        # fresh dir: the OTHER command must not be authorized by this token
        assert _authorize(other, tmp_path) is not None


class TestShellRedirectIsolation:
    """A trailing shell redirect (`2>&1`) is tolerated by ALL the target
    extractors. The merge/close pr-number regex always anchored on the digit
    positional; the force-push and branch-delete positional parsers now truncate
    at the first benign terminator on the quote-masked view BEFORE counting
    positionals, so a redirect / continuation no longer inflates the count. A
    faithful single force-push / branch-delete with a trailing redirect therefore
    AUTHORIZES — the target is re-derived from the executable prefix, and the
    redirect filename is structurally outside the positional window, so it can
    never become the target (the over-block is fixed WITHOUT opening a #1032
    under-block; a wrong/extra real positional still counts off → REFUSE)."""

    def _seed_typed(self, tmp_path, context):
        from merge_guard_post import write_token
        assert write_token(context, token_dir=tmp_path) is not None

    def test_merge_with_redirect_still_authorizes(self, tmp_path):
        self._seed_typed(tmp_path, {"operation_type": "merge", "pr_number": "5"})
        assert _authorize("gh pr merge 5 2>&1", tmp_path) is None

    def test_branch_delete_with_redirect_authorizes(self, tmp_path):
        """A single-branch delete with a trailing `2>&1` redirect AUTHORIZES: the
        positional parser truncates at the redirect before counting, so the branch
        ref re-derives to `x` and matches the token. (Previously this over-blocked
        — the redirect was miscounted as a second positional; the over-block-
        tokenizer fix removes it without opening a #1032 under-block.)"""
        self._seed_typed(tmp_path, {"operation_type": "branch-delete", "branch": "x"})
        assert _authorize("git branch -D x 2>&1", tmp_path) is None
        # Companion: WITHOUT the redirect the same token also authorizes.
        assert _authorize("git branch -D x", tmp_path) is None

    def test_force_push_with_redirect_authorizes(self, tmp_path):
        """A faithful force-push with a trailing `2>&1` redirect AUTHORIZES: the ref
        parser truncates at the redirect before counting positionals, so the
        destination re-derives to `main` and matches the token. (Previously this
        over-blocked; the fix re-derives the target from the executable prefix and
        the redirect filename can never become the ref — no #1032 under-block.)"""
        self._seed_typed(tmp_path, {"operation_type": "force-push", "target_ref": "main"})
        assert _authorize("git push --force origin main 2>&1", tmp_path) is None
        # Companion: WITHOUT the redirect the same token also authorizes.
        assert _authorize("git push --force origin main", tmp_path) is None


class TestMintGuardBranches:
    """MINT guard branches under the minimal floor. The multiSelect refusal is a
    structural over-block guard (multiSelect never mints) that survives. The old
    label<->description op-consistency refuse (Step 4) was prose intent-parsing and
    was REMOVED — a wrong-OP label is now an ACCEPTED RESIDUAL (mints the command in
    the description); see test_label_op_inconsistent_with_command_mints_residual."""

    def test_multiselect_refused_as_mint_source(self, tmp_path):
        """multiSelect is refused as a mint SOURCE (structural over-block guard) — a
        multiSelect question never mints, regardless of its options' prose. Floor:
        this is the `if multi: continue` guard, NOT a decline-word veto."""
        q = "Select actions:"
        opts = [_opt("Merge it", "Run `gh pr merge 42`"), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts, multiSelect=True)], {q: "Merge it, Cancel"}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []
        assert _authorize("gh pr merge 42", tmp_path) is not None

    def test_label_op_inconsistent_with_command_mints_residual(self, tmp_path):
        """RESIDUAL-CANARY (floor): when the clicked option's LABEL names a DIFFERENT
        operation than the command in its description (label 'Close the PR', command
        `gh pr merge 42`), the mint MINTS the command — the floor trusts the clicked
        command and does NOT cross-check the label prose (a documented wrong-OP
        ACCEPTED RESIDUAL). NON-VACUITY (broaden-mutation): re-add a Step-4
        label<->command op-consistency refuse to `_mint_context_from_bundle` → this
        selection refuses → 0 tokens → RED."""
        q = "Proceed?"
        # Label says 'close', description carries a MERGE command — divergent op.
        opts = [_opt("Close the PR", "Run `gh pr merge 42`"), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts)], {q: "Close the PR"}, tmp_path)
        assert code == 0
        assert len(_minted_tokens(tmp_path)) == 1
        assert _authorize("gh pr merge 42", tmp_path) is None

    def test_label_op_consistent_with_command_mints(self, tmp_path):
        """Positive control: a label whose op AGREES with the command's op mints
        normally (proves the consistency guard isn't blanket-refusing)."""
        q = "Proceed?"
        opts = [_opt("Merge the PR", "Run `gh pr merge 42`"), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts)], {q: "Merge the PR"}, tmp_path)
        assert code == 0
        assert _authorize("gh pr merge 42", tmp_path) is None


class TestMultiTargetBranchDeleteUnderBlock:
    """HALT #29 (#1032-class under-block): a branch-delete token approved for ONE
    branch must NOT authorize a MULTI-target `git branch -D <approved> <other>` —
    the command also deletes the UNAPPROVED <other>. The pre-fix
    _extract_branch_name captured only the FIRST positional, so a single-branch
    token matched and authorized the multi-delete (the fossilized gap).

    CLASS-I, REVERT-COUPLED to the #30 _extract_branch_name multi-target fix:
    reverting #30 re-opens the gap → these two deny tests flip RED (RESTORE_CLEAN).
    RED-on-pre-#30-HEAD is the intended non-vacuity posture; they go GREEN once
    #30 lands."""

    @pytest.mark.parametrize("command", [
        "git branch -D feature other-important-branch",
        "git branch --delete --force feature other-important-branch",
    ])
    def test_single_branch_token_denies_multi_target_delete(self, tmp_path, command):
        """A token for 'feature' alone must NOT authorize a delete that ALSO
        removes 'other-important-branch'."""
        _seed_token(tmp_path, {"operation_type": "branch-delete", "branch": "feature"})
        assert _authorize(command, tmp_path) is not None

    def test_single_branch_token_still_authorizes_its_exact_single_target(self, tmp_path):
        """Positive control: the #30 fix must NOT over-block the legitimate
        single-target case — a token for 'feature' still authorizes the exact
        single-branch delete it approved."""
        _seed_token(tmp_path, {"operation_type": "branch-delete", "branch": "feature"})
        assert _authorize("git branch -D feature", tmp_path) is None


class TestOptionAnchoringMint:
    """#1032 F-REVIEW-1 (#32): the mint is ANCHORED to the operator's CLICKED
    option. A command in question PROSE alone (with a generic no-command clicked
    option), or any NO-OPTIONS / free-text bundle, NEVER mints — the operator
    only clicked a generic option, never the command.

    Revert-coupling (verified against #32 = 1771fd35):
      • padded-question counter-test → coupled to step-3b option-anchoring
        (revert step-3b → mints the question's command → flips RED).
      • no-options counter-tests → DEFENSE-IN-DEPTH: closed by BOTH step-0
        (no-options guard) AND step-3b (empty selected-option set → no pair in
        it). Reverting step-0 alone leaves step-3b as a backstop, so the
        no-options cases flip RED only under the WHOLE #32 revert (or step-0 +
        step-3b together) — documented in the HANDOFF.
    The CONTROLS stay GREEN under any #32 revert (they verify the fix does not
    over-block a legitimately option-carried approval)."""

    # ── COUNTER-TESTS (refuse the F-REVIEW-1 vulnerability) ──
    def test_padded_question_with_generic_option_refuses(self, tmp_path):
        """OPTION-MODE F-REVIEW-1 repro: a command padded into QUESTION prose +
        a GENERIC clicked option carrying NO command → NO mint. Revert #32's
        step-3b → the question's command mints → flips RED."""
        q = "Approve the change? (context: gh pr merge 9999 will run)"
        opts = [_opt("Yes, proceed", "go ahead"), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts)], {q: "Yes, proceed"}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []
        assert _authorize("gh pr merge 9999", tmp_path) is not None

    def test_no_options_bundle_with_command_in_question_refuses(self, tmp_path):
        """NO-OPTIONS guard: a bundle with no options anywhere + bare 'yes' to a
        command-bearing question → NO mint (no clicked option to anchor on).
        Defense-in-depth (step-0 + step-3b)."""
        q = "Should I run `gh pr merge 42`?"
        code = _invoke_post([_q(q)], {q: "yes"}, tmp_path)  # no options
        assert code == 0
        assert _minted_tokens(tmp_path) == []
        assert _authorize("gh pr merge 42", tmp_path) is not None

    def test_command_typed_in_free_text_answer_refuses(self, tmp_path):
        """A command typed into the FREE-TEXT answer (no options) is NOT a mint
        source → NO mint (the free-text arm is retired)."""
        q = "What should I run?"
        code = _invoke_post([_q(q)], {q: "yes, gh pr merge 42"}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []
        assert _authorize("gh pr merge 42", tmp_path) is not None

    # ── CONTROLS (no over-block — stay GREEN under any #32 revert) ──
    def test_command_in_clicked_option_mints(self, tmp_path):
        """Control: the conforming shape — command in the CLICKED option → mints."""
        q = "Approve?"
        opts = [_opt("Yes, merge", "Run `gh pr merge 42`"), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts)], {q: "Yes, merge"}, tmp_path)
        assert code == 0
        assert _authorize("gh pr merge 42", tmp_path) is None

    def test_echo_in_question_and_option_mints(self, tmp_path):
        """Control: command echoed in BOTH question AND the clicked option (same
        (op,target)) → ONE distinct pair, option-anchored → mints."""
        q = "Merge via `gh pr merge 42`?"
        opts = [_opt("Yes, merge", "Run `gh pr merge 42`"), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts)], {q: "Yes, merge"}, tmp_path)
        assert code == 0
        assert _authorize("gh pr merge 42", tmp_path) is None

    def test_divergent_question_vs_option_mints_clicked_residual(self, tmp_path):
        """RESIDUAL-CANARY (floor): the question names a DIFFERENT command
        (`git push --force origin main`) than the clicked option (`gh pr merge 42`).
        The minimal floor scans ONLY the CLICKED options for multiplicity (the
        question prose is never cross-checked), so it MINTS the clicked merge 42 and
        IGNORES the question's force-push — a documented wrong-TARGET ACCEPTED
        RESIDUAL, and the exact anchoring property this class guards (the mint binds
        to the clicked command, never question prose). The minted token authorizes
        merge 42; the divergent force-push is NOT authorized.
        NON-VACUITY (broaden-mutation): re-widen the Step-3 multiplicity scan to
        include the question text → two distinct pairs → refuse → 0 tokens → RED."""
        q = "Force-push via `git push --force origin main`?"
        opts = [_opt("Yes, merge", "Run `gh pr merge 42`"), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts)], {q: "Yes, merge"}, tmp_path)
        assert code == 0
        assert len(_minted_tokens(tmp_path)) == 1
        assert _authorize("gh pr merge 42", tmp_path) is None
        # the divergent question command is NOT authorized (only the clicked option)
        assert _authorize("git push --force origin main", tmp_path) is not None


class TestBenignChainFaithfulClickMints:
    """FUT-3 — the POST(mint) round-trip the read-side cannot see: a faithful single
    destructive op carrying a BENIGN continuation (`&& echo`, `| tee`, a trailing
    background `&`) is a faithful click, so the mint MINTS exactly one token AND the
    read side then AUTHORIZES the exact command end-to-end. The read-side suite already
    proves these benign chains are not-refused at read time (is_compound=False,
    is_dangerous=True); this closes the mint-side leg of the invariant — a faithful
    single-command click always mints and executes — through the real post.main() seam.

    NON-VACUITY (concrete, no source mutation): the SAME mint helper REFUSES a genuine
    >=2-destructive chain (0 tokens) — test_two_destructive_chain_does_not_mint below —
    so the positive 'mints' assertion is not a mint-everything tautology. A re-introduced
    compound/metachar over-block on the mint would drop the benign-chain token count to 0
    (RED); a read-side over-block would flip the authorize to DENIED (RED).
    """

    @pytest.mark.parametrize("benign_cmd", [
        "gh pr merge 42 && echo done",      # `&&` benign continuation
        "gh pr merge 42 | tee /tmp/log",    # pipe into a benign sink
        "gh pr merge 42 &",                 # trailing background operator
    ])
    def test_benign_continuation_mints_and_authorizes(self, tmp_path, benign_cmd):
        """A faithful click whose command carries a benign continuation mints exactly one
        token (single destructive leg) and the read side authorizes the matching command."""
        q = "Approve?"
        opts = [_opt("Yes, merge", f"Run `{benign_cmd}`"), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts)], {q: "Yes, merge"}, tmp_path)
        assert code == 0
        assert len(_minted_tokens(tmp_path)) == 1          # the mint MINTED the faithful click
        assert _authorize(benign_cmd, tmp_path) is None    # read AUTHORIZES end-to-end

    def test_two_destructive_chain_does_not_mint(self, tmp_path):
        """Discriminating negative (the non-vacuity anchor for the positive above): a
        genuine TWO-destructive chain is refused by the mint (>=2 destructive legs) → 0
        tokens, and the read side denies it too. Proves the benign-chain 'mints' assertion
        is coupled to single-op faithfulness, not a mint-everything no-op."""
        q = "Approve?"
        cmd = "gh pr merge 42 && gh pr close 7 --delete-branch"
        opts = [_opt("Yes, merge", f"Run `{cmd}`"), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts)], {q: "Yes, merge"}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []              # >=2 destructive → mint REFUSES
        assert _authorize(cmd, tmp_path) is not None       # read denies too


# ════════════════════════════════════════════════════════════════════════════
# #1042 — PRIVILEGED-FLAG BINDING over the real mint→read seam
# ════════════════════════════════════════════════════════════════════════════


class TestPrivilegedFlagMintSymmetry:
    """A1 — the MINT must scan a surface WIDER than the quote-truncated
    bare-command region for bound flags: the command's own LEG within the
    selected-option text (the leg-bounded window; formerly the full option
    text). A privileged flag positioned AFTER a quoted argument in a BARE
    command falls outside locate_command_regions' truncated region (it stops at
    the first quote) but INSIDE the command's leg; the mint's flag_scan_text
    widening recovers it so the approved set carries the flag and a faithful
    re-execution AUTHORIZES while an added flag REFUSES.

    These drive the REAL mint (post.main → write_token → check_merge_authorization)
    — a hand-built token would bypass the mint widening and be vacuous for the A1
    claim. Revert-coupling: the AUTHORIZE round-trip is coupled to the C3 mint
    widening (merge_guard_post.py). A SOURCE-ONLY revert re-mints a flagless token
    (the truncated region drops the flag) while the read side still scans the full
    command → {} != {--admin} → REFUSE → the round-trip flips RED (measured
    cardinality in the TEST HANDOFF)."""

    _CMD_ADMIN_AFTER_QUOTE = 'gh pr merge 5 --subject "ship it" --admin'

    def test_admin_after_quoted_arg_round_trips_via_full_mint_scan(self, tmp_path):
        """Approve a BARE command with --admin after a quoted --subject; the mint
        scans the command's leg of the option text to bind --admin, so the
        faithful re-execution AUTHORIZES. (Without the wider-than-region scan the
        mint binds {} and this flips to REFUSE.)"""
        q = "Merge this pull request now? The reviewers have signed off."
        opts = [
            _opt("Yes, merge", "On approval run: " + self._CMD_ADMIN_AFTER_QUOTE),
            _opt("Cancel", "Abort"),
        ]
        assert _invoke_post([_q(q, opts)], {q: "Yes, merge"}, tmp_path) == 0
        assert len(_minted_tokens(tmp_path)) == 1
        assert _authorize(self._CMD_ADMIN_AFTER_QUOTE, tmp_path) is None

    def test_admin_added_after_quote_at_exec_is_blocked(self, tmp_path):
        """Bypass direction through the real seam: approve the command WITHOUT
        --admin (subject only); execute WITH --admin appended after the quote. The
        read side scans the full command and catches the added --admin the approval
        never carried → REFUSE (coupled to the C2 read gate)."""
        approved = 'gh pr merge 5 --subject "ship it"'
        q = "Merge this pull request now?"
        opts = [_opt("Yes, merge", "On approval run: " + approved), _opt("Cancel", "Abort")]
        assert _invoke_post([_q(q, opts)], {q: "Yes, merge"}, tmp_path) == 0
        assert len(_minted_tokens(tmp_path)) == 1
        assert _authorize(self._CMD_ADMIN_AFTER_QUOTE, tmp_path) is not None

    def test_cross_repo_redirect_blocked_end_to_end(self, tmp_path):
        """Headline bug end-to-end: approve a bare merge (no -R) through the real
        mint; an execution that adds -R victim/repo REFUSES (the cross-repo
        redirect can no longer ride the checkpoint undetected)."""
        q = "Merge this pull request now?"
        opts = [_opt("Yes, merge", "On approval run: gh pr merge 5"), _opt("Cancel", "Abort")]
        assert _invoke_post([_q(q, opts)], {q: "Yes, merge"}, tmp_path) == 0
        assert len(_minted_tokens(tmp_path)) == 1
        assert _authorize("gh --repo victim/repo pr merge 5", tmp_path) is not None

    # ── non-merge op-class real-mint witness (removes the op-agnostic-transfer
    # dependency for a 2nd op + the value-carrying -R flag): the mint flag-scan is
    # ONE SSOT call site, but witnessing CLOSE end-to-end through the REAL mint
    # removes the need to ARGUE that merge coverage transfers. The _seed_token
    # read-floor cases below FORGE the token and bypass the mint; these drive the
    # real close mint.
    #
    # NB force-push real-mint witnesses: an earlier decline/defer prose veto once
    # blocked --no-verify approvals from minting ("no" substring), which is why
    # this class's force-push coverage went through _seed_token forgeries in
    # TestPrivilegedFlagReadFloorSeam. That veto was REMOVED in the pure-floor
    # mint redesign (no decline-intent parsing remains anywhere in
    # _mint_context_from_bundle), and empirically TODAY a --no-verify approval
    # MINTS through the real seam — TestLegBoundedMintWindow below carries the
    # real-mint force-push witnesses (round-trip + escalation canaries). The
    # seeded read-floor cases remain valid as forged-token read-path coverage. ──
    # NB the close commands carry --delete-branch: that is the close-danger
    # op-TRIGGER (a bare `gh pr close` is NOT a governed/held op), so a governed
    # close that the read arm holds must carry it. --delete-branch is bound via
    # op_type (NOT in the denylist), so it does NOT appear in bound_flags; -R does.
    def test_close_repo_round_trips_via_real_mint(self, tmp_path):
        """Approve a governed CLOSE bundle carrying -R through the REAL mint → the
        minted token binds --repo → the faithful re-execution AUTHORIZES. Witnesses
        the mint flag-scan on a non-merge op-class with a value-carrying flag."""
        cmd = "gh pr close 5 --delete-branch -R owner/repo"
        q = "Close PR 5 and delete its branch now?"
        opts = [_opt("Yes, close", "On approval run: " + cmd), _opt("Cancel", "Abort")]
        assert _invoke_post([_q(q, opts)], {q: "Yes, close"}, tmp_path) == 0
        assert len(_minted_tokens(tmp_path)) == 1
        assert _authorize(cmd, tmp_path) is None

    def test_close_repo_redirect_added_at_exec_blocked_via_real_mint(self, tmp_path):
        """Bypass direction through the real close mint: approve a governed close
        WITHOUT -R; an execution that ADDS -R victim/repo REFUSES (coupled to the
        C2 read gate)."""
        q = "Close PR 5 and delete its branch now?"
        opts = [
            _opt("Yes, close", "On approval run: gh pr close 5 --delete-branch"),
            _opt("Cancel", "Abort"),
        ]
        assert _invoke_post([_q(q, opts)], {q: "Yes, close"}, tmp_path) == 0
        assert len(_minted_tokens(tmp_path)) == 1
        assert _authorize(
            "gh pr close 5 --delete-branch -R victim/repo", tmp_path
        ) is not None


def _token_bound_flags(tmp) -> list:
    """The single minted token's bound_flags, sorted. Exactly-one-token asserted:
    exact-set inspection is what distinguishes WHICH side of the #1042
    set-equality moved (outcome-only assertions cannot)."""
    toks = _minted_tokens(tmp)
    assert len(toks) == 1, f"expected exactly one minted token, got {len(toks)}"
    data = json.loads(toks[0].read_text())
    ctx = data.get("context", data)
    return sorted(ctx.get("bound_flags", []))


class TestLegBoundedMintWindow:
    """#1083 — the mint's privileged-flag scan is bounded to the destructive
    command's own LEG within the option text (_leg_bounded_flag_scan_surface),
    and the read bind seam gained a two-tier fallback (_single_destructive_leg →
    _single_detectable_leg → whole command), restoring mint==read bind symmetry
    for every compound shape. Pre-fix, the FULL-text mint scan bound flag
    literals from benign continuation legs, which (a) DENIED the byte-identical
    re-execution of a faithful benign-continuation approval — a live over-block —
    and (b) opened an approval-laundering channel: the escalated single-command
    execution's read-side bind matched the polluted mint set (seam-proven for
    close, where `gh pr close 42 --delete-branch` deletes a real branch). Both
    surfaces now run the SAME two-tier leg selection, so they converge on the
    op's own leg BY CONSTRUCTION (the symmetry pin below guards the shared-helper
    wiring). Every test drives the REAL mint (post.main) and the REAL read seam;
    the counter-mutations prove the canaries are coupled to each half of the
    single-commit fix.

    #1087 update: the CLOSE channel is now closed more fundamentally at the READ
    FLOOR — the close danger arms match PER-LEG (_CLOSE_LITERAL_ARMS), so the
    ambiguous cross-leg close member is is_dangerous=False and mints NOTHING (the
    two-tier bind is no longer REACHED by a dangerous close). The close-member
    pins below therefore assert mint-nothing / run-free rather than a []-bind. The
    two-tier bind is RETAINED for the push/merge/force channels (which still
    produce real tokens) and as defense-in-depth for a future isolable op — the
    tier helpers stay exercised by test_two_tier_selection_precedence, and the
    invariant that justifies retention is pinned by the OPEN-Q D tripwire
    (TestEmergentDangerClassIsCloseOnly)."""

    # --- cured members: byte-identical re-approval AUTHORIZES (pre-fix RED) ---

    @pytest.mark.parametrize(
        "member",
        [
            "git push origin main && echo --force-with-lease",   # push-to-main member
            "gh pr merge 42 && echo --admin",                    # pre-existing merge member
            "git push --force origin main && echo --no-verify",  # force-push member
            "git push origin main && echo --force-with-leas",    # abbreviation x window
        ],
    )
    def test_cured_member_byte_identical_reapproval_authorizes(self, member, tmp_path):
        """The cured over-block: the read side binds from the isolated destructive
        leg, so the full-text mint bind of the echo literal made the BYTE-IDENTICAL
        faithful re-execution refuse. The window binds [] — EXACTLY — and the
        faithful compound authorizes."""
        q = "Proceed with this operation?"
        opts = [_opt("Yes, run it", f"On approval run: `{member}`"), _opt("Cancel", "Abort")]
        assert _invoke_post([_q(q, opts)], {q: "Yes, run it"}, tmp_path) == 0
        assert _token_bound_flags(tmp_path) == []
        assert _authorize(member, tmp_path) is None

    def test_next_line_flag_literal_no_longer_binds(self, tmp_path):
        """A flag literal on a LATER LINE of the option description (newline = leg
        boundary) no longer binds; the faithful command authorizes."""
        cmd = "git push origin main"
        q = "Push to main?"
        opts = [
            _opt("Yes, push", f"On approval run: `{cmd}`\nNote: never uses --force-with-lease"),
            _opt("Cancel", "Abort"),
        ]
        assert _invoke_post([_q(q, opts)], {q: "Yes, push"}, tmp_path) == 0
        assert _token_bound_flags(tmp_path) == []
        assert _authorize(cmd, tmp_path) is None

    # --- the closed escalation channel, per reachable op-class ---

    @pytest.mark.parametrize(
        "member,escalated",
        [
            ("git push origin main && echo --force-with-lease",
             "git push --force-with-lease origin main"),
            ("gh pr merge 42 && echo --admin",
             "gh pr merge 42 --admin"),
            ("git push --force origin main && echo --no-verify",
             "git push --force origin main --no-verify"),
            ("git push origin main && echo --force-with-leas",
             "git push --force-with-lease origin main"),
        ],
    )
    def test_escalation_channel_closed(self, member, escalated, tmp_path):
        """Approval-laundering closed: pre-fix, approving the plain op + a flag
        literal in a benign continuation leg minted a FLAGGED token whose set
        matched the escalated single-command execution's read-side bind →
        AUTHORIZE. Post-fix the token binds [] → the escalation REFUSES."""
        q = "Proceed?"
        opts = [_opt("Yes, run it", f"On approval run: `{member}`"), _opt("Cancel", "Abort")]
        assert _invoke_post([_q(q, opts)], {q: "Yes, run it"}, tmp_path) == 0
        assert _token_bound_flags(tmp_path) == []
        assert _authorize(escalated, tmp_path) is not None

    # --- close op-class: the emergent members are now not-dangerous per-leg ---

    @pytest.mark.parametrize(
        "member",
        [
            "gh pr close 42 && echo --delete-branch",   # forward close arm
            "echo --delete-branch && gh pr close 42",   # reversed close arm
        ],
    )
    def test_close_emergent_member_mints_nothing_and_runs_free(self, member, tmp_path):
        """Post-#1087 the close danger arms match PER-LEG at the read floor, so
        neither shape has `gh pr close` and `--delete-branch` together in ONE leg
        — is_dangerous=False. The member is no longer an emergent-danger compound:
        the approval mints NOTHING (write-gate: not_dangerous) and the
        byte-identical re-execution RUNS FREE (a reversible bare close plus an
        echo). Both shapes cure identically because the per-leg floor is
        position-independent — the reversed shape (leg[0] is the echo) is no
        longer a special case, it too has no in-leg close+flag. This is the #1087
        over-block removal; the two-tier bind that formerly bound [] here is no
        longer reached by a dangerous close (retained as defense-in-depth per
        OPEN-Q A)."""
        q = "Close it?"
        opts = [_opt("Yes, close", f"On approval run: `{member}`"), _opt("Cancel", "Abort")]
        _invoke_post([_q(q, opts)], {q: "Yes, close"}, tmp_path)
        assert len(_minted_tokens(tmp_path)) == 0
        assert _authorize(member, tmp_path) is None

    def test_close_laundering_channel_closed(self, tmp_path):
        """THE HEADLINE (co-headline with the push channel, for the sec-review):
        pre-fix, approving `gh pr close 42 && echo --delete-branch` — which reads
        as a reversible bare close plus an echo — minted
        bound_flags=['--delete-branch'] from the echo literal, and the ESCALATED
        `gh pr close 42 --delete-branch` (a REAL, irreversible branch-deleting
        close) AUTHORIZED against it. Post-#1087 the mechanism is more
        fundamental: the member is is_dangerous=False per-leg, so the approval
        mints NOTHING (not a []-token via the two-tier bind) and the escalated
        single REFUSES for lack of any token. The explicit zero-mint assertion
        makes this non-vacuous — a regression that re-minted the member would flip
        it RED at the count, not silently pass on the DENY."""
        member = "gh pr close 42 && echo --delete-branch"
        q = "Close it?"
        opts = [_opt("Yes, close", f"On approval run: `{member}`"), _opt("Cancel", "Abort")]
        assert _invoke_post([_q(q, opts)], {q: "Yes, close"}, tmp_path) == 0
        assert len(_minted_tokens(tmp_path)) == 0
        assert _authorize("gh pr close 42 --delete-branch", tmp_path) is not None

    def test_close_flag_on_op_single_leg_round_trips(self, tmp_path):
        """No-over-isolation control + wrapper-strip-order pin: the HONEST
        branch-deleting close puts the flag ON the op — the window binds it and
        the faithful re-execution authorizes. The flag is the last token before
        the closing backtick, so a window composed BEFORE the wrapper strip would
        glue the backtick onto the flag and bind [] — flipping this RED."""
        cmd = "gh pr close 42 --delete-branch"
        q = "Close and delete branch?"
        opts = [_opt("Yes, close", f"On approval run: `{cmd}` now"), _opt("Cancel", "Abort")]
        assert _invoke_post([_q(q, opts)], {q: "Yes, close"}, tmp_path) == 0
        assert _token_bound_flags(tmp_path) == ["--delete-branch"]
        assert _authorize(cmd, tmp_path) is None

    def test_bare_close_boundary_stays_ungated(self, tmp_path):
        """Boundary control: bare `gh pr close 42` is reversible and UNGATED —
        the approval mints nothing (not dangerous) and the execution runs free
        without consulting a token."""
        cmd = "gh pr close 42"
        q = "Close it?"
        opts = [_opt("Yes, close", f"On approval run: `{cmd}`"), _opt("Cancel", "Abort")]
        _invoke_post([_q(q, opts)], {q: "Yes, close"}, tmp_path)
        assert len(_minted_tokens(tmp_path)) == 0
        assert _authorize(cmd, tmp_path) is None

    # --- no-under-bind: op in a NON-FIRST leg carrying its REAL flag ---

    @pytest.mark.parametrize(
        "cmd,expected_flags",
        [
            ("echo hi && gh pr close 42 --delete-branch", ["--delete-branch"]),
            ("cd /repo && gh pr merge 42 --admin", ["--admin"]),
            ("gh pr close 42 --delete-branch && echo x", ["--delete-branch"]),
        ],
    )
    def test_op_leg_flag_binds_wherever_the_op_leg_sits(self, cmd, expected_flags, tmp_path):
        """No-under-bind: when the op's OWN leg carries the privileged flag, the
        two-tier selection binds it in ANY leg position. A positional leg[0]
        window under-bound the op-in-non-first-leg forms (window = the benign
        first leg → mint [] vs read [flag] → the faithful click OVER-BLOCKED);
        the two-tier selection cures that direction too."""
        q = "Proceed?"
        opts = [_opt("Yes, run it", f"On approval run: `{cmd}`"), _opt("Cancel", "Abort")]
        assert _invoke_post([_q(q, opts)], {q: "Yes, run it"}, tmp_path) == 0
        assert _token_bound_flags(tmp_path) == expected_flags
        assert _authorize(cmd, tmp_path) is None

    # --- ambiguity fail-safe: the #1087 laundering-closed proof (real seam) ---

    def test_ambiguous_approval_source_mints_nothing_and_escalated_single_denies(self, tmp_path):
        """#1087 laundering closed, end-to-end over the REAL mint + read seams:
        pre-fix, approving the ambiguous multi-close
        `gh pr close 42 && gh pr close 43 && echo --delete-branch` (which reads as
        two reversible bare closes plus an echo) minted a token whose set
        AUTHORIZED the escalated single `gh pr close 42 --delete-branch` (a real
        branch delete). Post-fix the ambiguous source is is_dangerous=False
        per-leg → the approval mints NOTHING, and the escalated single-command
        REFUSES for lack of any token. This is the approval-SOURCE direction the
        #1083 close pins never exercised (they drove the member as an execution
        target); it is the direct laundering-closed proof."""
        ambiguous = "gh pr close 42 && gh pr close 43 && echo --delete-branch"
        q = "Close it?"
        opts = [_opt("Yes, close", f"On approval run: `{ambiguous}`"), _opt("Cancel", "Abort")]
        _invoke_post([_q(q, opts)], {q: "Yes, close"}, tmp_path)
        assert len(_minted_tokens(tmp_path)) == 0
        assert _authorize("gh pr close 42 --delete-branch", tmp_path) is not None

    def test_two_tier_selection_precedence(self):
        """Destructive-path precedence, at the tier helpers directly: a dangerous
        leg wins (tier 1 — the merge member never consults tier 2); tier 2
        isolates the unique detectable leg for the emergent close member; a
        non-unique detectable set abstains to None (→ whole-command)."""
        from shared.merge_guard_common import (
            _single_destructive_leg,
            _single_detectable_leg,
        )
        assert _single_destructive_leg("gh pr merge 42 && echo --admin") == "gh pr merge 42"
        assert _single_destructive_leg("gh pr close 42 && echo --delete-branch") is None
        assert _single_detectable_leg("gh pr close 42 && echo --delete-branch") == "gh pr close 42"
        assert _single_detectable_leg(
            "gh pr close 42 && gh pr close 43 && echo --delete-branch"
        ) is None

    # --- mint/read symmetry: shared two-tier selection (anti-rot pin) ---

    def test_mint_window_and_read_seam_share_the_two_tier_selection(self):
        """The anti-parallel-path-rot pin: the mint window must SELECT its scan
        leg with the SAME shared helpers the read seam uses — not a positional or
        reimplemented variant that can drift. Structural: the window's source
        references both tier helpers. Behavioral: for every compound shape the
        window's selected surface carries exactly the flags the read-side
        two-tier leg carries (the set-equality substrate)."""
        import inspect
        from merge_guard_post import _leg_bounded_flag_scan_surface, _strip_command_wrapper
        from shared.merge_guard_common import (
            _single_destructive_leg,
            _single_detectable_leg,
            extract_command_context,
        )

        src = inspect.getsource(_leg_bounded_flag_scan_surface)
        assert "_single_destructive_leg" in src and "_single_detectable_leg" in src, (
            "mint window no longer routes through the shared two-tier helpers — "
            "parallel-path rot risk on the authorize path"
        )
        for cmd in [
            "gh pr close 42 && echo --delete-branch",
            "echo --delete-branch && gh pr close 42",
            "echo hi && gh pr close 42 --delete-branch",
            "gh pr merge 42 && echo --admin",
            "cd /repo && gh pr merge 42 --admin",
            "gh pr close 42 --delete-branch",
        ]:
            window = _leg_bounded_flag_scan_surface(
                _strip_command_wrapper(f"Yes Run `{cmd}` now"), cmd
            )
            read_leg = _single_destructive_leg(cmd) or _single_detectable_leg(cmd) or cmd
            mint_flags = sorted(
                extract_command_context(cmd, flag_scan_text=window).get("bound_flags", [])
            )
            read_flags = sorted(
                extract_command_context(read_leg).get("bound_flags", [])
            )
            assert mint_flags == read_flags, (
                f"mint/read bind diverged for {cmd!r}: {mint_flags} != {read_flags}"
            )

    # --- push-to-main window controls ---

    def test_trailing_lease_flag_binds_through_window(self, tmp_path):
        """Truncation-hazard + wrapper-strip order pin: the faithful spelling with
        the flag AFTER the positionals must keep binding — a window truncated at
        the target, or composed BEFORE the wrapper strip (gluing the closing
        backtick onto the trailing flag), would bind [] and flip the round-trip
        to REFUSE. Second direction: the lease approval must NOT authorize a
        plain push (set-equality both ways)."""
        cmd = "git push origin main --force-with-lease"
        q = "Push?"
        opts = [_opt("Yes, push", f"On approval run: `{cmd}` now"), _opt("Cancel", "Abort")]
        assert _invoke_post([_q(q, opts)], {q: "Yes, push"}, tmp_path) == 0
        assert _token_bound_flags(tmp_path) == ["--force-with-lease"]
        assert _authorize(cmd, tmp_path) is None
        assert _authorize("git push origin main", tmp_path) is not None

    def test_value_spelling_compound_preserved_allow(self, tmp_path):
        """Preserved-ALLOW control: the =value lease spelling + an echo lease
        literal — the op leg's =value normalizes to the same bare canonical the
        echo literal added, so set semantics collapse it and this compound
        authorized before AND after the window. Pins that the window does not
        disturb an already-correct compound."""
        member = "git push --force-with-lease=main:abc123 origin main && echo --force-with-lease"
        q = "Push?"
        opts = [_opt("Yes, push", f"On approval run: `{member}`"), _opt("Cancel", "Abort")]
        assert _invoke_post([_q(q, opts)], {q: "Yes, push"}, tmp_path) == 0
        assert _token_bound_flags(tmp_path) == ["--force-with-lease"]
        assert _authorize(member, tmp_path) is None

    def test_compound_refuse_upstream_of_bind(self, tmp_path):
        """Compound-refuse orthogonality: >=2 destructive legs still refuse at the
        mint, upstream of any flag binding."""
        member = "gh pr merge 5 && git branch -Df victim"
        q = "Proceed?"
        opts = [_opt("Yes", f"On approval run: `{member}`"), _opt("Cancel", "Abort")]
        _invoke_post([_q(q, opts)], {q: "Yes"}, tmp_path)
        assert len(_minted_tokens(tmp_path)) == 0

    # --- non-vacuity: in-memory counter-mutations, both halves, both directions ---

    def test_window_is_non_vacuous_under_full_text_mutation(self, tmp_path, monkeypatch):
        """MINT-half counter-mutation: monkeypatch the window back to the PRE-FIX
        full-text surface (identity — the module-global binding Step 5 resolves
        at call time) and assert the laundering channel RE-OPENS: the mint binds
        the echo literal and the escalated execution AUTHORIZES. Proves the
        channel-closed canaries are coupled to the window."""
        member = "git push origin main && echo --force-with-lease"
        escalated = "git push --force-with-lease origin main"
        q = "Push?"
        opts = [_opt("Yes, push", f"On approval run: `{member}`"), _opt("Cancel", "Abort")]
        # direction 1 — fix present: bind [] and the escalation refuses
        assert _invoke_post([_q(q, opts)], {q: "Yes, push"}, tmp_path) == 0
        assert _token_bound_flags(tmp_path) == []
        assert _authorize(escalated, tmp_path) is not None
        # direction 2 — pre-fix surface restored: the channel re-opens
        import merge_guard_post as post_mod
        monkeypatch.setattr(post_mod, "_leg_bounded_flag_scan_surface", lambda text, cmd: text)
        tmp2 = tmp_path / "prefix-sim"
        tmp2.mkdir()
        assert _invoke_post([_q(q, opts)], {q: "Yes, push"}, tmp2) == 0
        assert _token_bound_flags(tmp2) == ["--force-with-lease"], (
            "full-text mutation did not restore the pre-fix echo-literal bind — "
            "the channel-closed canaries would be vacuous"
        )
        assert _authorize(escalated, tmp2) is None, (
            "full-text mutation did not re-open the laundering channel"
        )

    def test_close_laundering_closed_is_non_vacuous_under_whole_command_match(
            self, tmp_path, monkeypatch):
        """READ-FLOOR non-vacuity for the #1087 close per-leg conversion — replaces
        the tier-2-neutering non-vacuity, which is void now that is_dangerous is
        per-leg and independent of tier 2. Direction 1 — fix present: the
        ambiguous multi-close is is_dangerous=False, mints nothing, and the
        escalated single denies. Direction 2 — restore the PRE-FIX whole-command
        close match by neutering the leg substrate to a single whole-command
        "leg" (identity slice), so the per-leg close arms fire over the WHOLE
        stripped command again: the ambiguous source becomes is_dangerous=True,
        the approval MINTS, and the escalated single AUTHORIZES — the laundering
        channel RE-OPENS. Proves the close-channel canaries are coupled to the
        per-leg conversion, not vacuously green."""
        from shared.merge_guard_common import is_dangerous_command
        ambiguous = "gh pr close 42 && gh pr close 43 && echo --delete-branch"
        escalated = "gh pr close 42 --delete-branch"
        q = "Close it?"
        opts = [_opt("Yes, close", f"On approval run: `{ambiguous}`"), _opt("Cancel", "Abort")]
        # direction 1 — fix present: not-dangerous, mints nothing, escalation denies
        assert is_dangerous_command(ambiguous) is False
        _invoke_post([_q(q, opts)], {q: "Yes, close"}, tmp_path)
        assert len(_minted_tokens(tmp_path)) == 0
        assert _authorize(escalated, tmp_path) is not None
        # direction 2 — pre-fix whole-command close match restored: laundering re-opens
        import shared.merge_guard_common as common_mod
        monkeypatch.setattr(common_mod, "_slice_stripped_legs", lambda s: [s])
        assert is_dangerous_command(ambiguous) is True, (
            "identity-slice did not restore the whole-command close match — "
            "the close-channel canaries would be vacuous"
        )
        tmp2 = tmp_path / "prefix-sim"
        tmp2.mkdir()
        _invoke_post([_q(q, opts)], {q: "Yes, close"}, tmp2)
        assert len(_minted_tokens(tmp2)) == 1, (
            "whole-command close match did not restore the pre-fix mint — "
            "the close laundering-closed canary would be vacuous"
        )
        assert _authorize(escalated, tmp2) is None, (
            "whole-command close match did not re-open the laundering channel"
        )


class TestPrivilegedFlagReadFloorSeam:
    """The read floor over the REAL on-disk token seam: a forged/seeded approval
    token with a given bound_flags set is read back by check_merge_authorization
    and must REFUSE any privileged-flag escalation while AUTHORIZing the exact
    match. _seed_token writes the token directly (bypassing the write gate) —
    exactly the hand-crafted-token threat the read floor must withstand.

    Revert-coupling: every REFUSE here is coupled to the C2 set-equality gate
    (merge_guard_pre.py); a source-only revert flips each to AUTHORIZE → RED. The
    AUTHORIZE controls confirm the refusal is the FLAG axis (op+target already
    match), not an unrelated mismatch."""

    def test_flagless_token_denies_admin_execution(self, tmp_path):
        _seed_token(tmp_path, {"operation_type": "merge", "pr_number": "5", "bound_flags": []})
        assert _authorize("gh pr merge 5 --admin", tmp_path) is not None

    def test_flagless_token_denies_cross_repo_redirect(self, tmp_path):
        _seed_token(tmp_path, {"operation_type": "merge", "pr_number": "5", "bound_flags": []})
        assert _authorize("gh --repo victim/repo pr merge 5", tmp_path) is not None

    def test_admin_token_authorizes_matching_admin(self, tmp_path):
        """Positive control over the seam: the matching --admin execution AUTHORIZES
        — so the refusals above are the flag axis, not a target/op mismatch."""
        _seed_token(
            tmp_path,
            {"operation_type": "merge", "pr_number": "5", "bound_flags": ["--admin"]},
        )
        assert _authorize("gh pr merge 5 --admin", tmp_path) is None

    def test_flagless_token_denies_no_verify_force_push(self, tmp_path):
        _seed_token(
            tmp_path,
            {"operation_type": "force-push", "target_ref": "main", "bound_flags": []},
        )
        assert _authorize("git push --no-verify origin main --force", tmp_path) is not None

    def test_flagless_token_denies_git_no_verif_abbreviation(self, tmp_path):
        """SECURITY-LOAD-BEARING end-to-end: git accepts the `--no-verif`
        abbreviation (really disabling the pre-push hook); the read floor expands
        it on the git surface so a flagless approval REFUSES it (a missed
        abbreviation would be a silent UNDER-block)."""
        _seed_token(
            tmp_path,
            {"operation_type": "force-push", "target_ref": "main", "bound_flags": []},
        )
        assert _authorize("git push --no-verif origin main --force", tmp_path) is not None

    def test_no_verify_token_authorizes_matching_force_push(self, tmp_path):
        """Positive control: the matching --no-verify execution AUTHORIZES."""
        _seed_token(
            tmp_path,
            {"operation_type": "force-push", "target_ref": "main",
             "bound_flags": ["--no-verify"]},
        )
        assert _authorize("git push --no-verify origin main --force", tmp_path) is None


# ════════════════════════════════════════════════════════════════════════════
# Bash line-continuation parity over the real mint→read seam.
# A `\<newline>` in the SELECTED option text must be JOINED identically on the
# mint arm (locate_command_regions + extract_command_context) and the read arm,
# so the #1042 op/target/flag bind is continuation-INVARIANT. Without the shared
# normalization a `\<newline>`-split danger trigger / privileged flag drifts the
# two arms apart in BOTH directions: a faithful click is OVER-blocked, and a
# split privileged flag UNDER-slips a flagless approval.
# ════════════════════════════════════════════════════════════════════════════


class TestLineContinuationMintReadSymmetry:
    """Bash line-continuation (`\\<newline>`) in the SELECTED option text, driven
    through the REAL mint→read seam (post.main → write_token →
    check_merge_authorization) — never a hand-built token that would bypass the
    mint decision. The shared SSOT now joins `\\<newline>` → space on BOTH
    locate_command_regions (the op/target region) and extract_command_context (the
    flag scan) BEFORE detection, so mint and read normalize identically.

    Both directions are coupled to the common.py line-continuation normalization:
    a SOURCE-ONLY revert of that fix (leave this test file in place, restore
    merge_guard_common.py to its pre-fix shape) flips every test in this class RED
    — expected cardinality {3 failed} — and each row's docstring names the precise
    pre-fix drift that breaks it.

      OVER-BLOCK FIX (faithful click authorizes): a faithful close/merge whose
      option text carries a `\\<newline>`-split danger trigger / privileged flag
      mints a token binding the JOINED command, and the clean executed command
      AUTHORIZES end-to-end (mint==read round-trip closes).

      UNDER-BLOCK GUARD (escalation refused): a plain `merge 5` token (no --admin)
      does NOT authorize a `\\<newline>`-split `--admin` execution — the read side
      now joins the continuation, binds {--admin}, and the set-inequality
      {} != {--admin} REFUSES, STRENGTHENING the #1042 bind (a split privileged
      flag no longer slips)."""

    # Bash `\<newline>` line continuation embedded in the option text. The shell
    # joins this to a single clean command on execution; the guard must bind the
    # JOINED command so a faithful click round-trips and a split flag cannot drift.
    _CLOSE_SPLIT = "gh pr close 5 \\\n--delete-branch"
    _MERGE_ADMIN_SPLIT = "gh pr merge 5 \\\n--admin"

    def test_close_split_continuation_round_trips_via_real_mint(self, tmp_path):
        """OVER-BLOCK FIX (op/target region axis): a faithful
        `gh pr close 5 \\<newline>--delete-branch` click mints exactly one token
        binding the joined close+--delete-branch, and the clean executed
        `gh pr close 5 --delete-branch` AUTHORIZES.

        NON-VACUITY (source-only revert of the common.py fix): pre-fix
        locate_command_regions truncates the region at the newline to the
        NON-governed `gh pr close 5 \\` (a bare close without --delete-branch is not
        a held op) → the mint withholds the token → len(tokens) == 0 → this row
        RED (an OVER-block the read side then denies the full command for)."""
        q = "Close PR 5 and delete its branch now?"
        opts = [_opt("Yes, close", "On approval run: " + self._CLOSE_SPLIT),
                _opt("Cancel", "Abort")]
        assert _invoke_post([_q(q, opts)], {q: "Yes, close"}, tmp_path) == 0
        assert len(_minted_tokens(tmp_path)) == 1
        assert _authorize("gh pr close 5 --delete-branch", tmp_path) is None

    def test_merge_admin_split_continuation_binds_and_round_trips(self, tmp_path):
        """OVER-BLOCK FIX (privileged-flag bind axis): a faithful
        `gh pr merge 5 \\<newline>--admin` click mints a token binding {--admin} (the
        fix joins the split flag into extract_command_context's flag scan), and the
        clean executed `gh pr merge 5 --admin` AUTHORIZES.

        NON-VACUITY (source-only revert): pre-fix extract_command_context truncates
        the flag scan at the newline → binds {} → the read side scans the clean
        command and binds {--admin} → {} != {--admin} → the authorize flips to
        REFUSE → this row RED."""
        q = "Merge PR 5 now? The reviewers have signed off."
        opts = [_opt("Yes, merge", "On approval run: " + self._MERGE_ADMIN_SPLIT),
                _opt("Cancel", "Abort")]
        assert _invoke_post([_q(q, opts)], {q: "Yes, merge"}, tmp_path) == 0
        assert len(_minted_tokens(tmp_path)) == 1
        assert _authorize("gh pr merge 5 --admin", tmp_path) is None

    def test_plain_token_denies_split_admin_execution(self, tmp_path):
        """UNDER-BLOCK GUARD: approve a plain `gh pr merge 5` (no --admin) through
        the REAL mint, then execute the `\\<newline>`-split
        `gh pr merge 5 \\<newline>--admin`. The read side joins the continuation,
        binds {--admin}, and the plain token's {} != {--admin} → REFUSE — proving
        the SSOT fix STRENGTHENS the #1042 bind: a split privileged flag no longer
        slips a flagless approval.

        NON-VACUITY (source-only revert): pre-fix extract_command_context truncates
        the EXECUTED command at the newline → binds {} → {} == {} → AUTHORIZES the
        split --admin escalation (the latent under-block) → this assertion
        (`is not None`) flips RED. The clean-form refusal is the pre-existing #1042
        bind (asserted in TestPrivilegedFlagReadFloorSeam); the SPLIT form is the
        line-continuation-specific leg this test owns."""
        q = "Merge PR 5 now?"
        opts = [_opt("Yes, merge", "On approval run: gh pr merge 5"),
                _opt("Cancel", "Abort")]
        assert _invoke_post([_q(q, opts)], {q: "Yes, merge"}, tmp_path) == 0
        # The plain `merge 5` faithfully mints (binds {} — no privileged flag).
        assert len(_minted_tokens(tmp_path)) == 1
        # The split-admin execution must NOT ride that flagless token.
        assert _authorize(self._MERGE_ADMIN_SPLIT, tmp_path) is not None


# ════════════════════════════════════════════════════════════════════════════
# #1049 — veto-word SUBSTRING in the approval command literal (floor reconcile).
# The post-#22 minimal floor does NO decline/defer intent-parsing at all, so a
# command literal whose text contains a decline-word SUBSTRING — `no` in
# `--no-verify`, `review` in a `feature/review-ui` branch, `pending` in
# `release/pending-qa` — simply MINTS via plain anchoring (#1042 binding). There is
# no veto to false-trip and nothing to "scope": the whole _veto_text / decline-veto
# mechanism the original #1049 tests exercised was REMOVED (#21→#22). What remains
# worth pinning is that these specific FLAG / BRANCH-NAME forms still mint+bind
# correctly — the positive teeth below. The decline/defer-label, wrong-op and
# wrong-target scenarios are now ACCEPTED RESIDUALS, asserted as residual-canaries
# in TestDeclineLabelCarryingCommandResidualMints / TestMintGuardBranches /
# TestOptionAnchoringMint above.
# ════════════════════════════════════════════════════════════════════════════


class TestVetoWordSubstringFormsMint1049:
    """POSITIVE teeth (floor): a backticked command literal whose command text
    contains a decline-word SUBSTRING — `no` in `--no-verify`, `review` in a
    `feature/review-ui` branch, `pending` in `release/pending-qa` — MINTS and
    authorizes end-to-end. Under the minimal floor there is NO decline-detection, so
    there is nothing to false-trip: these mint by plain anchoring + #1042 binding
    like any command. (The original #1049 FP — a decline-veto self-tripping on the
    substring — and its `_veto_text` fix no longer exist; these forms are kept as
    binding positives.) NON-VACUITY: a regression in anchoring or the #1042
    flag/branch binding flips the authorize assertion (the --no-verify bound flag /
    the force-push target_ref must round-trip exactly)."""

    @pytest.mark.parametrize("question, label, description, command, veto_word", [
        # `no` in --no-verify; valueless flag → binds cleanly → authorizes.
        ("Merge this PR now? Reviewers signed off.", "Yes, merge",
         "Run `gh pr merge 42 --no-verify`", "gh pr merge 42 --no-verify", "no"),
        # `review` in a branch name; force-push target_ref round-trips cleanly.
        ("Force-push the rebased branch?", "Approve force-push",
         "Run `git push --force origin feature/review-ui`",
         "git push --force origin feature/review-ui", "review"),
        # `pending` in a branch name; force-push target_ref round-trips cleanly.
        ("Force-push the staging branch?", "Approve force-push",
         "Run `git push --force origin release/pending-qa`",
         "git push --force origin release/pending-qa", "pending"),
    ])
    def test_substring_in_command_literal_mints_and_authorizes(
        self, tmp_path, question, label, description, command, veto_word
    ):
        """The veto-word substring sits INSIDE the backticked command literal; the
        floor does no decline-detection, so the bundle MINTS by plain anchoring and
        the command AUTHORIZES end-to-end over the real token seam (the flag/branch
        binds exactly)."""
        code = _invoke_post(
            [_q(question, [_opt(label, description), _opt("Cancel", "Abort")])],
            {question: label}, tmp_path,
        )
        assert code == 0
        assert len(_minted_tokens(tmp_path)) == 1, (
            f"#1049: '{veto_word}' substring in the approval command self-vetoed"
        )
        assert _authorize(command, tmp_path) is None

    def test_repo_flag_value_substring_mints(self, tmp_path):
        """`review` in a `--repo owner/review-repo` value MINTS via plain anchoring
        (the floor does no decline-detection). Asserts the MINT; #1059 (folded into
        the floor) space-strips the wrapping backtick from the mint's flag-scan
        surface (_strip_command_wrapper) so the bound `--repo` value matches the read
        side's bare-command scan. NON-VACUITY: a regression in anchoring or the
        GAP1 is_dangerous write-gate flips the mint to 0 tokens → RED."""
        q = "Merge this PR on the review fork now?"
        desc = "Run `gh pr merge 99 --repo owner/review-repo`"
        code = _invoke_post(
            [_q(q, [_opt("Yes, merge", desc), _opt("Cancel", "Abort")])],
            {q: "Yes, merge"}, tmp_path,
        )
        assert code == 0
        assert len(_minted_tokens(tmp_path)) == 1
