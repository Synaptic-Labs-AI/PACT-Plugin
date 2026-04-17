"""
Integration tests for #453 session_consolidated detection.

These tests exercise the full journal write → journal read → detector
decision chain without mocking `read_events`. Unlike the isolated-unit
tests in test_session_end.py::TestCheckUnpausedPr (which patch
`session_end.read_events` with synthetic event lists), these tests write
real events to a tmp-path journal via append_event and let the detector
read them back. This pins the plumbing end-to-end so a regression that
corrupts the journal path, the event serialization, or the event-type
filter — any of which would pass the unit tests — is caught here.

Coverage:

T23 regression (session 9097e100 scenario):
 1. /PACT:wrap-up after PR merged mid-session → no warning
 2. /PACT:pause with consolidation → no warning (pause-path symmetry)
 3. Multiple /PACT:wrap-up runs in one session (idempotent, N events) → no warning

Adversarial D5 ordering invariants:
 4. Stale session_consolidated (ts < review_dispatch ts) still short-circuits
    — architect's design is pure existence, not timestamp comparison
 5. session_consolidated with no ts field still short-circuits
 6. Multiple review_dispatch events + one session_consolidated → no warning

AC#4 (no-network) end-to-end guarantee:
 7. When session_consolidated is present, check_pr_state MUST NOT be called
    (pin via subprocess.run patch that would flake if invoked against a
    fixture PR number)

AC#2 end-to-end (Fix A defense-in-depth):
 8. review_dispatch present, no session_consolidated, gh reports MERGED → no warning
 9. review_dispatch present, no session_consolidated, gh reports CLOSED → no warning
10. review_dispatch present, no session_consolidated, gh reports OPEN → warning fires

AC#3 true-positive preservation:
11. review_dispatch present, no session_consolidated, no pause, gh fails → warning fires
12. Legacy pause-covers-review path still works with no session_consolidated event
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

# Absolute path to session_journal.py for subprocess-level bash tests. Computed
# here so the bash-template tests (TestPauseBashConditionalEmission,
# TestUnsubstitutedPlaceholderFailureMode) do not need to re-derive it.
_PACT_PLUGIN_DIR = Path(__file__).parent.parent
_SESSION_JOURNAL_PY = str(_PACT_PLUGIN_DIR / "hooks" / "shared" / "session_journal.py")


# ---------------------------------------------------------------------------
# Fixtures — real journal writes into tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture
def session_dir(tmp_path):
    """Return a concrete session-directory path (string, not yet created)."""
    return str(tmp_path / ".claude" / "pact-sessions" / "test-project" / "test-session-id")


@pytest.fixture(autouse=True)
def _redirect_journal_path(monkeypatch, session_dir):
    """Point both write and read paths at the tmp session_dir.

    session_journal._get_session_dir() is the single hook the journal
    uses to resolve the implicit journal path (via _journal_path ->
    session_dir + "session-journal.jsonl"). Monkeypatching it for the
    module under test routes append_event + read_events at the tmp
    path without needing a full pact_context initialization dance.
    """
    import shared.session_journal as sj
    monkeypatch.setattr(sj, "_get_session_dir", lambda: session_dir)


@pytest.fixture(autouse=True)
def _default_check_pr_state():
    """Default: fake gh as OPEN so tests that do NOT patch it explicitly
    (i.e. the "session_consolidated present → short-circuit" cases) do
    not shell out to real gh against fixture PR numbers.

    Individual tests that exercise the Fix A MERGED/CLOSED/OPEN branches
    patch session_end.check_pr_state with their own return_value (inner
    patch wins over the fixture).
    """
    with patch("session_end.check_pr_state", return_value="OPEN"):
        yield


def _write_event(event_type, **fields):
    """Helper: construct + append a real journal event.

    Uses make_event + append_event so the test exercises the real
    serializer, schema validator, and atomic write path — not a bare
    file write. This catches regressions in the write path (e.g. a
    schema change that silently drops an event) that a synthetic
    read_events mock would hide.
    """
    from shared.session_journal import append_event, make_event

    event = make_event(event_type, **fields)
    assert append_event(event) is True, (
        f"append_event failed for {event_type!r} — test fixture setup "
        f"is broken, not the detector under test."
    )
    return event


# ---------------------------------------------------------------------------
# T23 regression: session 9097e100 scenario end-to-end
# ---------------------------------------------------------------------------


class TestSessionConsolidatedRegression:
    """End-to-end pins for the canonical #453 false-positive scenarios.

    Session 9097e100 (2026-04-17) was the originating case for #453:
    PR #447 was merged mid-session, /PACT:wrap-up ran, SessionEnd still
    surfaced the "PR is open but pause-mode was not run" warning. These
    tests reproduce the event sequence with real journal writes.
    """

    def test_t23_wrap_up_after_pr_merged_mid_session(self, session_dir):
        """T23 canonical regression: wrap-up after PR merged → no warning.

        Event sequence matches session 9097e100's wrap-up run:
        1. review_dispatch for PR #447 (user dispatched review earlier)
        2. session_end (wrap-up step 5 drain-before-close)
        3. session_consolidated (wrap-up step 5 new write — the #453 fix)
        4. SessionEnd hook fires check_unpaused_pr
        """
        from session_end import check_unpaused_pr

        _write_event(
            "review_dispatch",
            pr_number=447,
            pr_url="https://github.com/org/repo/pull/447",
            reviewers=["backend-coder"],
        )
        _write_event("session_end")
        _write_event("session_consolidated", **{"pass": 2, "task_count": 7, "memories_saved": 3})

        warning = check_unpaused_pr(tasks=None, project_slug="test-project")

        assert warning is None, (
            f"T23 regression: wrap-up after merged PR must not warn. "
            f"Got: {warning!r}"
        )

    def test_t23_pause_with_consolidation(self, session_dir):
        """Pause-path symmetry: /PACT:pause with consolidation → no warning.

        Pins that Fix B covers the pause path as well as the wrap-up
        path — a session that paused after consolidating must not warn
        regardless of the review_dispatch / session_paused timestamp
        ordering.
        """
        from session_end import check_unpaused_pr

        _write_event(
            "review_dispatch",
            pr_number=100,
            pr_url="https://github.com/org/repo/pull/100",
            reviewers=["backend-coder"],
        )
        _write_event("session_consolidated", **{"pass": 2, "task_count": 3, "memories_saved": 1})
        _write_event(
            "session_paused",
            pr_number=100,
            pr_url="https://github.com/org/repo/pull/100",
            branch="feat/test",
            worktree_path="/tmp/wt",
            consolidation_completed=True,
        )

        warning = check_unpaused_pr(tasks=None, project_slug="test-project")

        assert warning is None

    def test_t23_multiple_wrap_up_runs_in_one_session(self, session_dir):
        """Multiple session_consolidated events (N-run wrap-up) → no warning.

        A session that ran /PACT:wrap-up more than once (e.g. user
        cancelled the first attempt, ran it again after fixing a
        blocker) will have N session_consolidated events in the
        journal. The detector's `if read_events(...)` falsy-check is
        a truthy list → still short-circuits.
        """
        from session_end import check_unpaused_pr

        _write_event(
            "review_dispatch",
            pr_number=200,
            pr_url="https://github.com/org/repo/pull/200",
            reviewers=["backend-coder"],
        )
        _write_event("session_consolidated", **{"pass": 2, "task_count": 1})
        _write_event("session_consolidated", **{"pass": 2, "task_count": 5})
        _write_event("session_consolidated", **{"pass": 2, "task_count": 7})

        warning = check_unpaused_pr(tasks=None, project_slug="test-project")

        assert warning is None


# ---------------------------------------------------------------------------
# Adversarial D5 ordering invariants
# ---------------------------------------------------------------------------


class TestSessionConsolidatedOrderingInvariants:
    """Pin that the Fix B short-circuit is pure existence, not timestamp-based.

    D5 ordering in the architect's design: the short-circuit fires on
    the MERE EXISTENCE of any session_consolidated event. If a future
    refactor "optimizes" this by adding a timestamp comparison (e.g.
    "only short-circuit if session_consolidated is newer than
    review_dispatch"), these tests will fail — caught before the
    regression ships.
    """

    def test_stale_session_consolidated_still_short_circuits(self, session_dir):
        """Adversarial: session_consolidated ts < review_dispatch ts → still no warning.

        Simulates a session where an earlier wrap-up ran (leaving a
        stale session_consolidated event), and a later review_dispatch
        fired for a new PR. Under pure-existence semantics, the
        detector MUST short-circuit.

        If a regression added a timestamp guard (e.g. `last_consolidated
        >= last_review`), this test would surface a warning. The
        architect's D5 rationale explicitly chose pure existence to
        avoid this failure mode.
        """
        from session_end import check_unpaused_pr

        # Stale consolidated event (older timestamp).
        _write_event(
            "session_consolidated",
            ts="2026-01-01T00:00:00Z",
            **{"pass": 2, "task_count": 1},
        )
        # Newer review dispatch.
        _write_event(
            "review_dispatch",
            ts="2026-04-17T12:00:00Z",
            pr_number=555,
            pr_url="https://github.com/org/repo/pull/555",
            reviewers=["backend-coder"],
        )

        warning = check_unpaused_pr(tasks=None, project_slug="test-project")

        assert warning is None, (
            f"Stale session_consolidated must still short-circuit "
            f"(pure existence, not timestamp). Got: {warning!r}"
        )

    def test_multiple_review_dispatches_with_one_consolidated(self, session_dir):
        """Multiple review_dispatch events + one session_consolidated → no warning.

        Pins that a session with multiple PR dispatches where
        consolidation ran (even once) is treated as consolidated
        overall. Regression guard against a bad fix that tried to
        "match" consolidated events to specific review events by PR
        number.
        """
        from session_end import check_unpaused_pr

        _write_event(
            "review_dispatch",
            pr_number=101,
            pr_url="https://github.com/org/repo/pull/101",
            reviewers=["backend-coder"],
        )
        _write_event(
            "review_dispatch",
            pr_number=202,
            pr_url="https://github.com/org/repo/pull/202",
            reviewers=["backend-coder"],
        )
        _write_event(
            "review_dispatch",
            pr_number=303,
            pr_url="https://github.com/org/repo/pull/303",
            reviewers=["backend-coder"],
        )
        _write_event("session_consolidated", **{"pass": 2})

        warning = check_unpaused_pr(tasks=None, project_slug="test-project")

        assert warning is None


# ---------------------------------------------------------------------------
# AC#4 end-to-end (no network for wrap-up path)
# ---------------------------------------------------------------------------


class TestSessionConsolidatedNoNetworkGuarantee:
    """End-to-end pin of AC#4: wrap-up path invokes zero gh subprocess calls.

    Architectural guarantee: when session_consolidated is in the
    journal, check_pr_state is never reached because the short-circuit
    runs first. These tests patch subprocess.run in the real gh_helpers
    module and assert zero invocations — catching a regression that
    re-ordered the short-circuit after the gh call.
    """

    def test_wrap_up_path_invokes_zero_gh_calls(self, session_dir):
        """AC#4 end-to-end: session_consolidated present → subprocess.run NOT called.

        Patches shared.gh_helpers.subprocess.run (the real source of
        gh invocations) and asserts it was never called. The autouse
        _default_check_pr_state fixture normally intercepts at the
        session_end.check_pr_state boundary; this test drops that mock
        by overriding with a real pass-through so we can observe what
        the native code would have done.

        A regression that moved the check_pr_state call above the
        session_consolidated short-circuit (or deleted the short-
        circuit) would surface here as subprocess.run being called.
        """
        from session_end import check_unpaused_pr

        _write_event(
            "review_dispatch",
            pr_number=777,
            pr_url="https://github.com/org/repo/pull/777",
            reviewers=["backend-coder"],
        )
        _write_event("session_consolidated", **{"pass": 2})

        # Override the autouse check_pr_state mock with the REAL
        # gh_helpers.check_pr_state import so we can patch the
        # underlying subprocess.run and assert zero calls.
        from shared.gh_helpers import check_pr_state as real_check_pr_state

        mock_subprocess = MagicMock()
        with patch("session_end.check_pr_state", real_check_pr_state), \
             patch("shared.gh_helpers.subprocess.run") as mock_run:
            mock_run.side_effect = AssertionError(
                "AC#4 violation: subprocess.run was called despite "
                "session_consolidated being in the journal"
            )
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="test-project",
            )

        assert warning is None
        assert mock_run.call_count == 0, (
            f"AC#4 violation: subprocess.run called {mock_run.call_count} "
            f"times when it should have been skipped by the short-circuit."
        )


# ---------------------------------------------------------------------------
# AC#2 end-to-end (Fix A defense-in-depth)
# ---------------------------------------------------------------------------


class TestLivePrStateFixAEndToEnd:
    """End-to-end Fix A: live gh check catches merged/closed PRs with no wrap-up.

    These tests cover the scenario where neither Fix B (no
    session_consolidated in journal) nor the legacy pause-vs-review
    path caught the merged/closed state, and the last-line-of-defense
    gh call must make the correct decision.
    """

    def test_merged_pr_no_consolidation_short_circuits(self, session_dir):
        """AC#2: review dispatched, no consolidation, gh says MERGED → no warning."""
        from session_end import check_unpaused_pr

        _write_event(
            "review_dispatch",
            pr_number=447,
            pr_url="https://github.com/org/repo/pull/447",
            reviewers=["backend-coder"],
        )
        # Note: NO session_consolidated, NO session_paused.

        with patch("session_end.check_pr_state", return_value="MERGED"):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="test-project",
            )

        assert warning is None

    def test_closed_pr_no_consolidation_short_circuits(self, session_dir):
        """AC#2 sibling: gh says CLOSED → no warning."""
        from session_end import check_unpaused_pr

        _write_event(
            "review_dispatch",
            pr_number=448,
            pr_url="https://github.com/org/repo/pull/448",
            reviewers=["backend-coder"],
        )

        with patch("session_end.check_pr_state", return_value="CLOSED"):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="test-project",
            )

        assert warning is None

    def test_open_pr_no_consolidation_warns(self, session_dir):
        """True-positive: gh says OPEN + no consolidation → warning fires."""
        from session_end import check_unpaused_pr

        _write_event(
            "review_dispatch",
            pr_number=449,
            pr_url="https://github.com/org/repo/pull/449",
            reviewers=["backend-coder"],
        )

        with patch("session_end.check_pr_state", return_value="OPEN"):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="test-project",
            )

        assert warning is not None
        assert "PR #449" in warning

    def test_gh_offline_no_consolidation_conservative_warns(self, session_dir):
        """Fail-open: gh returns "" (offline / missing) → warn conservatively.

        End-to-end pin of the fail-open contract: if we cannot
        distinguish "offline" from "PR actually open," we keep the
        warning. Regression guard against a bad fix that treats the
        empty sentinel as "safe to skip."
        """
        from session_end import check_unpaused_pr

        _write_event(
            "review_dispatch",
            pr_number=500,
            pr_url="https://github.com/org/repo/pull/500",
            reviewers=["backend-coder"],
        )

        with patch("session_end.check_pr_state", return_value=""):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="test-project",
            )

        assert warning is not None
        assert "PR #500" in warning


# ---------------------------------------------------------------------------
# AC#3 true-positive preservation + legacy path preservation
# ---------------------------------------------------------------------------


class TestTruePositivePreservation:
    """Ensure the fix did not swallow genuine warning cases.

    AC#3 (true-positive preservation): a session that dispatched review
    but did NOT run consolidation must still warn. Tests here write
    only the events that existed before the fix and assert the warning
    still fires.
    """

    def test_legacy_pause_covers_review_still_works(self, session_dir):
        """M2 pinned: pause ts >= review ts → legacy no-warning path preserved."""
        from session_end import check_unpaused_pr

        _write_event(
            "review_dispatch",
            ts="2026-04-17T10:00:00Z",
            pr_number=600,
            pr_url="https://github.com/org/repo/pull/600",
            reviewers=["backend-coder"],
        )
        _write_event(
            "session_paused",
            ts="2026-04-17T11:00:00Z",
            pr_number=600,
            pr_url="https://github.com/org/repo/pull/600",
            branch="feat/test",
            worktree_path="/tmp/wt",
            consolidation_completed=True,
        )

        # Default check_pr_state autouse returns OPEN; this path
        # should never reach Fix A because the pause covers the review.
        warning = check_unpaused_pr(tasks=None, project_slug="test-project")

        assert warning is None

    def test_review_dispatch_without_consolidation_or_pause_warns(self, session_dir):
        """AC#3: pure true-positive case — dispatched review, quit without anything else."""
        from session_end import check_unpaused_pr

        _write_event(
            "review_dispatch",
            pr_number=700,
            pr_url="https://github.com/org/repo/pull/700",
            reviewers=["backend-coder"],
        )
        # No session_consolidated, no session_paused. Autouse check_pr_state is OPEN.

        warning = check_unpaused_pr(tasks=None, project_slug="test-project")

        assert warning is not None
        assert "PR #700" in warning


# ---------------------------------------------------------------------------
# Pre-existing journal compatibility
# ---------------------------------------------------------------------------


class TestPreExistingJournalCompat:
    """Journals written before the #453 fix must continue to validate.

    Architect's risk table: "Old journals without session_consolidated
    cause regressions — Likelihood: None (verified)". These tests pin
    that the append-only journal is forward-compatible: a journal
    written by a pre-fix session (zero session_consolidated events)
    produces the same detector output it always did.
    """

    def test_missing_journal_file_is_fail_open(self, session_dir):
        """Non-existent journal → read_events returns [] → legacy path runs.

        Simulates a session_end where the journal was never created
        (e.g. crashed before first append). read_events fail-opens to
        [] on every event_type query, so the detector treats it as "no
        consolidation, no review, no pause" → returns None (no PR to
        warn about).
        """
        from session_end import check_unpaused_pr

        # Do NOT write any events — journal file will not exist.
        warning = check_unpaused_pr(tasks=None, project_slug="test-project")

        assert warning is None

    def test_corrupt_journal_line_is_fail_open(self, session_dir):
        """Malformed JSON line → silently skipped; valid events parse.

        Matches session_journal.py's per-line fail-open contract:
        each event is self-contained; one bad line does not poison
        the scan. A corrupted session_consolidated line surfaces as
        "no consolidation event" which falls through to legacy logic
        — correctly conservative behavior.
        """
        from session_end import check_unpaused_pr

        # Write a valid review_dispatch first.
        _write_event(
            "review_dispatch",
            pr_number=800,
            pr_url="https://github.com/org/repo/pull/800",
            reviewers=["backend-coder"],
        )
        # Append a garbage line to the journal.
        journal_file = Path(session_dir) / "session-journal.jsonl"
        with journal_file.open("a", encoding="utf-8") as f:
            f.write("{this is not valid JSON\n")

        # Autouse check_pr_state returns OPEN. Garbage line skipped;
        # review_dispatch still visible; no session_consolidated →
        # falls through to Fix A → OPEN → warn.
        warning = check_unpaused_pr(tasks=None, project_slug="test-project")

        assert warning is not None
        assert "PR #800" in warning

    def test_pre_existing_journal_with_only_legacy_events_unchanged(self, session_dir):
        """Journal with no session_consolidated events → legacy detector behavior.

        Pins that a session whose journal predates the fix (only has
        the 2023-era events: session_start, agent_handoff,
        review_dispatch, session_paused) produces the same
        check_unpaused_pr output it produced before the fix landed.
        No behavioral regression on upgrade.
        """
        from session_end import check_unpaused_pr

        # Legacy events only — mirrors a journal from before #453.
        _write_event(
            "session_start",
            session_id="legacy-session",
            project_dir="/test/project",
        )
        _write_event(
            "review_dispatch",
            ts="2026-01-01T00:00:00Z",
            pr_number=900,
            pr_url="https://github.com/org/repo/pull/900",
            reviewers=["backend-coder"],
        )
        _write_event(
            "session_paused",
            ts="2026-01-02T00:00:00Z",
            pr_number=900,
            pr_url="https://github.com/org/repo/pull/900",
            branch="feat/legacy",
            worktree_path="/tmp/legacy-wt",
            consolidation_completed=True,
        )

        # Legacy pause-covers-review path: no warning, no gh call needed.
        warning = check_unpaused_pr(tasks=None, project_slug="test-project")

        assert warning is None


# ---------------------------------------------------------------------------
# Review cycle-1 L1: pause.md shell-guarded conditional emission
# ---------------------------------------------------------------------------


def _run_pause_bash(session_dir, *, true_or_false):
    """Execute the pause.md step-5 bash template against a tmp session_dir.

    Substitutes the shell-guard's {true_or_false} with the literal string
    argument so the conditional's `[ '<value>' = 'true' ]` gate takes the
    path under test. All other placeholders get safe test values so the
    session_paused write succeeds regardless of the guard branch.

    Returns the subprocess CompletedProcess so callers can assert on
    returncode, stdout, and stderr. The bash envelope mirrors pause.md's
    `set -e` + ERR trap exactly — if either write fails, this raises via
    the trap's `exit $rc` and the caller sees returncode != 0.
    """
    template = f"""set -e
trap 'rc=$?; echo "[JOURNAL WRITE FAILED] pause.md (bash line $LINENO): \\"${{BASH_COMMAND%%$'\\''\\n'\\''*}}\\" exit=$rc" >&2; exit $rc' ERR
if [ '{true_or_false}' = 'true' ]; then
  python3 "{_SESSION_JOURNAL_PY}" write \\
    --type session_consolidated --session-dir '{session_dir}' --stdin <<'JSON'
{{"pass": 2, "task_count": 7, "memories_saved": 3}}
JSON
fi
python3 "{_SESSION_JOURNAL_PY}" write \\
  --type session_paused --session-dir '{session_dir}' --stdin <<'JSON'
{{"pr_number": 123, "pr_url": "https://github.com/org/repo/pull/123", "branch": "feat/test", "worktree_path": "/tmp/wt", "consolidation_completed": {true_or_false}, "team_name": "pact-test"}}
JSON
"""
    return subprocess.run(
        ["bash", "-c", template],
        capture_output=True,
        text=True,
        timeout=15,
    )


class TestPauseBashConditionalEmission:
    """Pin the pause.md shell-guard at the bash-execution layer.

    Review cycle-1 L1 hardening: before this fix, pause.md's prose said
    the orchestrator MUST skip the session_consolidated write when
    consolidation did not run, but the bash heredoc emitted
    unconditionally. If the orchestrator copy-pasted the block verbatim
    ignoring the prose, a false-positive session_consolidated signal
    could suppress genuine warnings in a later session.

    These tests execute the real bash template with both branches of
    the guard and assert that the session_consolidated event is emitted
    ONLY when {true_or_false} = 'true'.
    """

    def test_guard_true_emits_both_events(self, tmp_path):
        """{true_or_false}='true' → both session_consolidated AND session_paused written."""
        session_dir = str(tmp_path)

        result = _run_pause_bash(session_dir, true_or_false="true")

        assert result.returncode == 0, (
            f"bash template failed under true branch: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

        journal = tmp_path / "session-journal.jsonl"
        assert journal.exists(), "journal file must exist after writes"
        lines = journal.read_text(encoding="utf-8").splitlines()
        types = [line.split('"type":"', 1)[1].split('"', 1)[0] for line in lines if line.strip()]
        assert "session_consolidated" in types, (
            f"true branch MUST emit session_consolidated. Got types: {types}"
        )
        assert "session_paused" in types, (
            f"true branch MUST emit session_paused. Got types: {types}"
        )

    def test_guard_false_emits_only_session_paused(self, tmp_path):
        """{true_or_false}='false' → session_consolidated SKIPPED, session_paused still written.

        Regression pin for review cycle-1 L1: removing the shell guard
        (restoring the unconditional write) causes this test to fail
        because session_consolidated would appear in the journal.
        """
        session_dir = str(tmp_path)

        result = _run_pause_bash(session_dir, true_or_false="false")

        assert result.returncode == 0, (
            f"bash template failed under false branch: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

        journal = tmp_path / "session-journal.jsonl"
        assert journal.exists(), "session_paused write must create the journal"
        lines = journal.read_text(encoding="utf-8").splitlines()
        types = [line.split('"type":"', 1)[1].split('"', 1)[0] for line in lines if line.strip()]
        assert "session_consolidated" not in types, (
            f"false branch MUST NOT emit session_consolidated. Got types: {types}"
        )
        assert "session_paused" in types, (
            f"false branch MUST still emit session_paused. Got types: {types}"
        )



# ---------------------------------------------------------------------------
# Review cycle-1 Fix 2: unsubstituted-placeholder failure is observable
# ---------------------------------------------------------------------------


class TestUnsubstitutedPlaceholderFailureMode:
    """Pin that unsubstituted {task_count}/{memories_saved} placeholders
    in the pause.md / wrap-up.md bash template produce a loud failure
    (non-zero exit + schema rejection on stderr), NOT a silent success
    or a successfully-written garbage event.

    Rationale: the orchestrator supplies `{task_count}` and
    `{memories_saved}` from the secretary's confirmation message. If
    those substitutions silently fail (e.g. a future refactor that
    swaps the template engine), the JSON body becomes
    `{"pass": 2, "task_count": {task_count}, "memories_saved": {memories_saved}}`
    which is not valid JSON. Under the current session_journal CLI
    contract, invalid stdin JSON triggers an "invalid --stdin JSON"
    stderr line and returncode 1 — set -e + ERR trap then aborts the
    outer bash block.

    These tests bind that observable-failure contract so a future
    change to the CLI that silently tolerated invalid JSON (e.g. a
    well-meaning "try to recover" branch) would be caught here.
    """

    def test_unsubstituted_placeholder_exits_nonzero(self, tmp_path):
        """Literal `{task_count}` in stdin JSON → session_journal write fails fast."""
        session_dir = str(tmp_path)

        # Manually construct the heredoc body with UNSUBSTITUTED placeholders
        # — mirrors what would happen if the orchestrator forgot to render
        # {task_count} / {memories_saved} before pasting the template.
        template = f"""set -e
python3 "{_SESSION_JOURNAL_PY}" write \\
  --type session_consolidated --session-dir '{session_dir}' --stdin <<'JSON'
{{"pass": 2, "task_count": {{task_count}}, "memories_saved": {{memories_saved}}}}
JSON
"""
        result = subprocess.run(
            ["bash", "-c", template],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0, (
            f"unsubstituted placeholders MUST cause non-zero exit; "
            f"got returncode=0 with stdout={result.stdout!r}"
        )
        assert "invalid" in result.stderr.lower() or "json" in result.stderr.lower(), (
            f"stderr MUST surface the JSON parse failure; got: {result.stderr!r}"
        )

    def test_unsubstituted_placeholder_writes_no_event(self, tmp_path):
        """Unsubstituted placeholders produce ZERO journal events.

        Complementary assertion to the non-zero exit pin: if a future
        refactor made the CLI silently drop invalid JSON but return 0,
        the prior test would pass spuriously. This test catches that
        failure mode by asserting the journal file is never created
        (the CLI returns before reaching `append_event`'s mkdir).
        """
        session_dir = str(tmp_path)

        template = f"""python3 "{_SESSION_JOURNAL_PY}" write \\
  --type session_consolidated --session-dir '{session_dir}' --stdin <<'JSON'
{{"pass": 2, "task_count": {{task_count}}, "memories_saved": {{memories_saved}}}}
JSON
"""
        subprocess.run(
            ["bash", "-c", template],
            capture_output=True,
            text=True,
            timeout=10,
        )

        journal = tmp_path / "session-journal.jsonl"
        assert not journal.exists(), (
            "unsubstituted-placeholder write MUST NOT create the journal "
            "file; schema validation or JSON parse must reject before "
            "reaching the filesystem."
        )
