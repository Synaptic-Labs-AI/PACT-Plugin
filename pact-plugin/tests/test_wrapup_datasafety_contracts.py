"""Regression-protecting presence pins for two data-safety contracts newly
added to commands/wrap-up.md:

  * Step 6 post-merge branch cleanup — a three-way PR-state split (MERGED /
    no-PR / unmerged) where a branch delete must NEVER fire without a verified
    MERGED state, each delete is single-target/single-leg, the remote is
    resolved with no hardcoded remote, and the main sync is fast-forward-only.

  * The single-save retrospective handoff — the orchestrator hands its
    retrospective to the secretary's in-flight consolidation as ONE memory
    write (not a second save task), guarded by a mandatory-exactly-one-signal
    hold so the secretary can neither finalize early (losing the retrospective)
    nor hang (waiting for a retrospective that was skipped).

PROSE-VACUITY CEILING (documented residual, not a gap): wrap-up.md is an
LLM-loaded command body and these are prose-level contracts — honored by an
LLM orchestrator / recognized by the runtime merge-guard hook. These are
therefore STRUCTURAL (presence + placement) assertions: they detect REMOVAL,
reordering, or a delete literal migrating into the wrong PR-state branch, but
they cannot prove an LLM obeys the instruction at runtime. That runtime
obedience is the architect's accepted option-B residual (the change touches no
hook; the merge-guard — untouched — remains the enforcement boundary). Static
presence pins are the appropriate coverage for a prose-only change whose logic
is not extracted to code.

Each test carries a DESIGN-INTENT note naming the drift it detects, so a
placement/label pin is not later mislabeled brittle: the branch labels and the
delete-literal-per-branch placement ARE the data-safety contract, so pinning
them is a deliberate drift detector, not over-fitting to churny wording.
"""
import re
from pathlib import Path

import pytest


WRAPUP_PATH = Path(__file__).parent.parent / "commands" / "wrap-up.md"

# The three PR-state branch labels in step 6. These labels ARE the structural
# spine of the data-safety split — pinning them detects a dropped/renamed
# branch (e.g. an unmerged PR losing its dedicated no-delete handling).
BRANCH_A_LABEL = "**A — PR is MERGED**"
BRANCH_B_LABEL = "**B — No PR exists**"
BRANCH_C_LABEL = "**C — PR exists but is not merged**"

# The two minted delete commands, with their runtime placeholders. The real
# form uses <branch>/<remote>; the prose's pedagogical "never bundle" example
# uses X/R placeholders, so it is deliberately distinct from these literals.
LOCAL_DELETE = "git branch -D <branch>"
REMOTE_DELETE = "git push <remote> --delete <branch>"

# Placeholder-agnostic destructive-verb patterns. The exact-literal pins above
# catch a verbatim copy of a minted delete migrating into the wrong PR-state
# branch; these catch a REWORDED / RE-PLACEHOLDERED delete (a different
# placeholder, a shell var, the long `--delete --force` form, a hardcoded
# remote, or the `push <remote> :<branch>` colon-refspec form) that the exact
# literals would miss. Deliberately CASE-SENSITIVE on the local verb: it matches
# the FORCE delete (`-D` / `--delete`) but NOT the SAFE lowercase `git branch
# -d`, which branch B legitimately names as the correct non-destructive outcome
# (worktree-cleanup's `-d` declines a not-fully-merged branch).
LOCAL_DELETE_VERB = re.compile(r"git\s+branch\s+(?:-D|--delete)\b")
REMOTE_DELETE_VERB = re.compile(r"git\s+push\b[^\n]*?(?:--delete\b|\s:\S)")


def _section(content, heading_prefix):
    """Return the body of the '## <heading_prefix>...' section, from the heading
    up to (but excluding) the next '## ' top-level heading.

    Asserting WITHIN a section localizes each contract to the step that owns it,
    so a delete literal migrating into the wrong step (or a signal-rule sliding
    out of step 4) trips — a whole-file grep would not."""
    marker = "## " + heading_prefix
    idx = content.find(marker)
    assert idx != -1, f"section heading not found: {marker!r}"
    body_start = idx + len(marker)
    nxt = content.find("\n## ", body_start)
    return content[body_start:] if nxt == -1 else content[body_start:nxt]


def _branch_slices(step6):
    """Split step 6 into its A/B/C PR-state branch slices.

    Raises if any branch label is missing or out of order — the three-way split
    IS the data-safety contract, so an absent branch is itself a failure."""
    a = step6.find(BRANCH_A_LABEL)
    b = step6.find(BRANCH_B_LABEL)
    c = step6.find(BRANCH_C_LABEL)
    assert a != -1 and b != -1 and c != -1, (
        "step 6 must contain all three PR-state branch labels (A merged / "
        "B no-PR / C unmerged)"
    )
    assert a < b < c, "PR-state branches must appear in A->B->C order"
    return step6[a:b], step6[b:c], step6[c:]


@pytest.fixture
def wrapup_content():
    return WRAPUP_PATH.read_text(encoding="utf-8")


@pytest.fixture
def step1(wrapup_content):
    return _section(wrapup_content, "1. Memory Consolidation")


@pytest.fixture
def step4(wrapup_content):
    return _section(wrapup_content, "4. Orchestration Retrospective")


@pytest.fixture
def step5(wrapup_content):
    return _section(wrapup_content, "5. Journal Drain-Before-Close")


@pytest.fixture
def step6(wrapup_content):
    return _section(wrapup_content, "6. Worktree Cleanup")


@pytest.fixture
def branches(step6):
    return _branch_slices(step6)


@pytest.fixture
def branch_a(branches):
    return branches[0]


@pytest.fixture
def branch_b(branches):
    return branches[1]


@pytest.fixture
def branch_c(branches):
    return branches[2]


class TestStep6BranchCleanupDataSafety:
    """Step 6 post-merge branch cleanup: the merged-gated delete sequence and
    the no-delete branches that preserve unmerged work."""

    def test_all_three_pr_state_branches_present_and_ordered(self, step6):
        # DESIGN-INTENT: the three-way PR-state split IS the data-safety
        # contract. A dropped or reordered branch label means a PR state lost
        # its dedicated handling — e.g. an unmerged PR falling through to the
        # delete sequence. Detects removal/reorder of any branch.
        a = step6.find(BRANCH_A_LABEL)
        b = step6.find(BRANCH_B_LABEL)
        c = step6.find(BRANCH_C_LABEL)
        assert a != -1, "MERGED (A) branch label missing from step 6"
        assert b != -1, "no-PR (B) branch label missing from step 6"
        assert c != -1, "unmerged (C) branch label missing from step 6"
        assert a < b < c, "PR-state branches must appear in A->B->C order"

    def test_merged_is_hard_precondition_for_deletes(self, branch_a):
        # DESIGN-INTENT: detects weakening of the verified-MERGED gate — the
        # single precondition that authorizes any delete. If this is removed a
        # future edit could delete on an unverified / closed-unmerged PR.
        assert 'state == "MERGED"' in branch_a
        assert "hard precondition for every delete" in branch_a

    def test_delete_literals_confined_to_merged_branch(
        self, branch_a, branch_b, branch_c
    ):
        # DESIGN-INTENT (lead-confirmed STRONGER structural proxy; a deliberate
        # drift detector, NOT over-fitting): a branch delete must NEVER fire
        # without a verified MERGED. Encoded structurally — both minted delete
        # literals appear ONLY in the MERGED (A) slice and are ABSENT from the
        # no-PR (B) and unmerged (C) slices. A weaker "MERGED appears somewhere
        # in step 6" would pass even if a delete leaked into B or C, which is
        # exactly the data-loss regression (deleting a branch that may hold
        # unmerged local work) this pin exists to catch.
        assert LOCAL_DELETE in branch_a, "local delete literal must live under the MERGED gate"
        assert REMOTE_DELETE in branch_a, "remote delete literal must live under the MERGED gate"
        assert LOCAL_DELETE not in branch_b, "local delete leaked into the no-PR branch"
        assert REMOTE_DELETE not in branch_b, "remote delete leaked into the no-PR branch"
        assert LOCAL_DELETE not in branch_c, "local delete leaked into the unmerged branch"
        assert REMOTE_DELETE not in branch_c, "remote delete leaked into the unmerged branch"

        # DESIGN-INTENT (verb-family hardening, NOT over-fitting): the exact
        # literals above only catch a VERBATIM copy of a minted delete migrating
        # into B/C. The pin's stated contract is broader — "a delete must NEVER
        # fire without a verified MERGED" — so a REWORDED / RE-PLACEHOLDERED
        # destructive verb leaking into B/C (e.g. `git branch -D <local>`,
        # `git branch --delete --force <branch>`, `git push origin --delete
        # <branch>`, `git push origin :<branch>`) is the SAME data-loss
        # regression and must also trip. Positive control first so the
        # confinement assertions can't degrade into a vacuous no-op if the delete
        # machinery is ever removed from slice A.
        assert LOCAL_DELETE_VERB.search(branch_a), "positive control: a local force-delete verb must exist in the MERGED slice"
        assert REMOTE_DELETE_VERB.search(branch_a), "positive control: a remote-delete verb must exist in the MERGED slice"
        assert not LOCAL_DELETE_VERB.search(branch_b), "a local delete verb (any wording) leaked into the no-PR branch"
        assert not REMOTE_DELETE_VERB.search(branch_b), "a remote delete verb (any wording) leaked into the no-PR branch"
        assert not LOCAL_DELETE_VERB.search(branch_c), "a local delete verb (any wording) leaked into the unmerged branch"
        assert not REMOTE_DELETE_VERB.search(branch_c), "a remote delete verb (any wording) leaked into the unmerged branch"

    def test_no_bundled_compound_delete(self, step6):
        # DESIGN-INTENT: the merge-guard mints a single-target, single-leg
        # delete; bundling the two deletes into one approval is refused by the
        # guard (over-blocking a faithful click) or ships an unrecognized
        # compound. Assert the two REAL minted literals are never joined into
        # one command. The prose's pedagogical anti-pattern uses X/R
        # placeholders — distinct from the real <branch>/<remote> form — so it
        # is intentionally not matched here.
        assert f"{LOCAL_DELETE} && {REMOTE_DELETE}" not in step6
        assert f"{LOCAL_DELETE}; {REMOTE_DELETE}" not in step6

    def test_no_pr_branch_preserves_branch(self, branch_b):
        # DESIGN-INTENT: a worktree with no PR may hold UNMERGED local work, so
        # its branch must be preserved (worktree cleanup only, no delete).
        # Detects a future edit that drops the preservation guarantee. Delete
        # ABSENCE for branch B is asserted in the confinement test above.
        assert "preserved" in branch_b
        assert "/PACT:worktree-cleanup" in branch_b

    def test_unmerged_branch_pauses_and_preserves_worktree(self, branch_c):
        # DESIGN-INTENT: an open / closed-unmerged PR must NOT trigger cleanup;
        # it writes session_paused and preserves the worktree so in-review work
        # is not discarded. Detects a future edit that lets an unmerged PR fall
        # through to worktree teardown or a delete.
        assert "Skip worktree cleanup" in branch_c
        assert "session_paused" in branch_c

    def test_fork_resolution_uses_no_hardcoded_remote(self, branch_a):
        # DESIGN-INTENT: the remote is resolved by owner/repo-slug match against
        # `git remote -v`, never a hardcoded `origin` / fork. A hardcoded remote
        # could push --delete against the wrong remote or fail on a fork PR.
        # Also a SACROSANCT generic-consumer requirement (shipped command bodies
        # must not hardcode the maintainer's own remote).
        assert "git remote -v" in branch_a
        assert "no hardcoded remote name" in branch_a

    def test_ff_only_main_sync_refuses_non_fast_forward(self, branch_a):
        # DESIGN-INTENT: the post-delete main sync is --ff-only so a divergent
        # history surfaces as an anomaly instead of a silent auto-merge/rebase.
        # Detects removal of the --ff-only guard or its report-and-stop clause.
        # "anomaly" alone is churny wording; the load-bearing SAFETY behavior is
        # "never auto-merge or rebase" — pin that so a reword that keeps the word
        # "anomaly" but drops the no-auto-resolve guarantee still trips.
        assert "git pull --ff-only origin main" in branch_a
        assert "anomaly" in branch_a
        assert "never auto-merge or rebase" in branch_a

    def test_step6_gated_on_step5_drain(self, step6):
        # DESIGN-INTENT: the ENFORCEMENT-POINT half of the concurrency ordering
        # invariant. test_concurrent_gated_split... pins the invariant in step 1
        # (the note) and step 5 (the drain gate); THIS pins the step-6 side — the
        # destructive step must itself declare it is gated on the step-5 drain,
        # so a future edit can't drop step 6's own guard while leaving step 5's
        # wording intact and silently let worktree teardown run before the
        # harvest has read what it would destroy.
        assert "gated on the step-5 drain-confirmation" in step6


class TestSingleSaveSignalContract:
    """The single-save retrospective handoff: hold-until-signal, the mandatory
    exactly-one-signal rule, graceful degradation, and the concurrent/gated
    split that keeps destructive steps behind the drain gate."""

    def test_consolidation_task_holds_until_signal(self, step1):
        # DESIGN-INTENT: the secretary holds the single consolidation write until
        # it receives EITHER the retrospective payload OR the skip-marker.
        # Removing the hold reverts to the completion-ordering race (the harvest
        # finalizes before the retrospective arrives -> two saves, or the
        # retrospective is lost).
        assert "Hold finalization of that write" in step1
        assert "EITHER the retrospective payload OR" in step1

    def test_graceful_degradation_never_hang_never_drop(self, step1):
        # DESIGN-INTENT: the worst-case bound — if neither signal arrives the
        # secretary finalizes without the retrospective, and a late payload is
        # saved as a follow-up write. Guarantees the hold can never hang and the
        # retrospective can never be silently dropped.
        assert "Graceful degradation" in step1
        assert "never hang, never drop" in step1

    def test_mandatory_exactly_one_end_of_step4_signal(self, step4):
        # DESIGN-INTENT: step 4 ALWAYS emits exactly one signal (payload on the
        # normal path, skip-marker on the trivial path). This is what makes the
        # message-based hold both race-free and deadlock-free. Removing the rule
        # lets the secretary hang (no signal) or finalize early (loses the
        # retrospective).
        assert "Always send exactly one end-of-step-4 signal" in step4

    def test_skip_path_sends_release_marker(self, step4):
        # DESIGN-INTENT: the trivial-session skip path MUST send the "no
        # retrospective this session" marker; it is the signal that releases the
        # secretary's hold when there is no retrospective. Load-bearing, not
        # cosmetic — without it a trivial session hangs the secretary forever.
        assert "Skip when" in step4
        assert "no retrospective this session" in step4

    def test_single_save_via_sendmessage_not_second_task(self, step4):
        # DESIGN-INTENT: the retrospective is folded into the in-flight
        # consolidation via SendMessage as ONE write — NOT a second save task.
        # Detects a reversion to the eliminated two-save shape.
        assert "SendMessage" in step4
        assert "do NOT create a second save task" in step4

    def test_concurrent_gated_split_only_destructive_steps_wait(self, step1, step5):
        # DESIGN-INTENT: the concurrency win removes a false serialization —
        # steps 2-4 run while the harvest is in flight; ONLY the destructive
        # steps (6/7) wait on the step-5 drain. The correctness invariant (no
        # destructive step runs before the harvest reads what it would destroy)
        # must survive in both the step-1 note and the step-5 gate. Detects a
        # future edit that re-serializes, or moves a destructive step above the
        # drain gate.
        correctness = "no destructive step may run before the harvest has read what it would destroy"
        assert "Concurrent, not serialized" in step1
        assert correctness in step1
        assert correctness in step5
        # step 5 is the single drain-gate that the destructive steps 6/7 wait on
        assert "step 6" in step5
        assert "step 7" in step5
