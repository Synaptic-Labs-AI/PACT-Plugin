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
    def test_wge1_decline_option_embedding_command_vetoes(self, tmp_path):
        """WGE-1: a DECLINE option ('Continue reviewing') whose description
        embeds the command, when SELECTED, vetoes the mint (veto precedence over
        command presence)."""
        q = "Ready to merge?"
        opts = [_opt("Yes, merge", "Run `gh pr merge 42`"),
                _opt("Continue reviewing", "Run `gh pr merge 42` after review")]
        code = _invoke_post([_q(q, opts)], {q: "Continue reviewing"}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []
        assert _authorize("gh pr merge 42", tmp_path) is not None

    def test_wge2_deferred_selection_carrying_command_vetoes(self, tmp_path):
        """WGE-2: a deferred selection ('Approve after CI') carrying the command
        is vetoed by the defer recognizer ('after')."""
        q = "Merge now or after CI?"
        opts = [_opt("Approve after CI", "Run `gh pr merge 42` once CI is green"),
                _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts)], {q: "Approve after CI"}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []

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


class TestClassIIDeclineLabelGuardrail:
    """The decline-label guardrail: selecting a decline option NEVER mints, even
    when a valid affirmative option carrying a command is also present. CLASS-II:
    selecting the decline label was non-affirmative at baseline too, so it is
    already-true and revert-immune.

    ADD-MUTATION PROOF: remove the Step-1 decline/defer veto from
    _mint_context_from_bundle. Then selecting 'Pause work for now' (whose
    description carries the command) lets the multiplicity gate see
    `gh pr merge 42` from the selected option → a token mints → the no-token
    assert flips RED. Verified manually via git-stash of the veto block."""

    def test_selecting_decline_label_mints_nothing(self, tmp_path):
        q = "Merge or pause?"
        opts = [
            _opt("Yes, merge", "Run `gh pr merge 42`"),
            _opt("Pause work for now", "Run `gh pr merge 42` later, not now"),
        ]
        code = _invoke_post([_q(q, opts)], {q: "Pause work for now"}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []
        assert _authorize("gh pr merge 42", tmp_path) is not None


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
    """A trailing shell redirect (`2>&1`) is tolerated by the merge/close target
    extractor (the pr-number regex anchors on the digit positional and ignores
    later tokens), but it DEFEATS the positional-counting ref parsers for BOTH
    force-push (`origin main 2>&1` = 3 positionals → unparseable → REFUSE) AND —
    post-#30 multi-target hardening — branch-delete (`-D x 2>&1` = 2 positionals
    → multi-target → REFUSE). Both over-blocks are the SAFE #1031 direction (NOT
    a #1032 under-block). Candidate follow-up (KD-6, security-owned): strip
    recognized shell redirections before the positional split — now covering
    force-push AND branch-delete."""

    def _seed_typed(self, tmp_path, context):
        from merge_guard_post import write_token
        assert write_token(context, token_dir=tmp_path) is not None

    def test_merge_with_redirect_still_authorizes(self, tmp_path):
        self._seed_typed(tmp_path, {"operation_type": "merge", "pr_number": "5"})
        assert _authorize("gh pr merge 5 2>&1", tmp_path) is None

    def test_branch_delete_with_redirect_over_blocks(self, tmp_path):
        """Post-#30: the `2>&1` redirect is counted as a second positional, so
        even a single-branch delete with a redirect conservatively OVER-BLOCKS
        (the branch ref is unparseable → REFUSE). SAFE #1031 direction, NOT a
        #1032 under-block. Same KD-6 redirect-strip follow-up class as force-push
        (the follow-up now covers branch-delete too)."""
        self._seed_typed(tmp_path, {"operation_type": "branch-delete", "branch": "x"})
        assert _authorize("git branch -D x 2>&1", tmp_path) is not None
        # Sanity: WITHOUT the redirect the same token authorizes (isolates the
        # over-block to the redirect-induced positional miscount).
        assert _authorize("git branch -D x", tmp_path) is None

    def test_force_push_with_redirect_over_blocks(self, tmp_path):
        """Even WITH a matching force-push token, the `2>&1` redirect makes the
        ref unparseable → conservative REFUSE (documented over-block)."""
        self._seed_typed(tmp_path, {"operation_type": "force-push", "target_ref": "main"})
        assert _authorize("git push --force origin main 2>&1", tmp_path) is not None
        # Sanity: WITHOUT the redirect the same token authorizes (isolates the
        # over-block to the redirect-induced positional miscount).
        assert _authorize("git push --force origin main", tmp_path) is None


class TestMintGuardBranches:
    """Two additional MINT guard branches (architect §5.2 steps 1 & 4): the
    multiSelect decline veto and the label<->description op-consistency refuse.
    Both are REFUSE-only #1031-direction guards (over-block, never authorize)."""

    def test_multiselect_with_decline_option_refuses(self, tmp_path):
        """Step 1 veto: a multiSelect question containing a decline option is
        refused as a mint source (the decline word vetoes the whole bundle),
        even though the selected option carries a command."""
        q = "Select actions:"
        opts = [_opt("Merge it", "Run `gh pr merge 42`"), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts, multiSelect=True)], {q: "Merge it, Cancel"}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []
        assert _authorize("gh pr merge 42", tmp_path) is not None

    def test_label_op_inconsistent_with_command_refuses(self, tmp_path):
        """Step 4 (D/1c): when the selected option's LABEL names a DIFFERENT
        operation than the command in its description, the mint REFUSES
        (prose-divergence-refuse-only — a loose label must never re-import the
        #1031 distractor by authorizing a mismatched command)."""
        q = "Proceed?"
        # Label says 'close', description carries a MERGE command — divergent op.
        opts = [_opt("Close the PR", "Run `gh pr merge 42`"), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts)], {q: "Close the PR"}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []
        assert _authorize("gh pr merge 42", tmp_path) is not None

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

    def test_divergent_question_vs_option_refuses(self, tmp_path):
        """Control: question op/target ≠ the clicked option's (two distinct
        pairs) → the multiplicity gate REFUSES (the #32 fix did not weaken
        divergence detection)."""
        q = "Force-push via `git push --force origin main`?"
        opts = [_opt("Yes, merge", "Run `gh pr merge 42`"), _opt("Cancel", "Abort")]
        code = _invoke_post([_q(q, opts)], {q: "Yes, merge"}, tmp_path)
        assert code == 0
        assert _minted_tokens(tmp_path) == []
