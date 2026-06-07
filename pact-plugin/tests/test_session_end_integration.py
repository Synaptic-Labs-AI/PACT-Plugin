"""
Location: pact-plugin/tests/test_session_end_integration.py
Summary: NON-MOCKED regression-pin for session_end's get_task_list consumption
         (caller 4 of the team-dir resolution fix). check_unpaused_pr's
         secondary task-scan fallback is the ONLY session_end consumer of
         get_task_list, and test_session_end.py exercises it ONLY against a
         MOCKED get_task_list (patched at ~20 sites) — so session_end's
         consumption of real team-dir-resolved tasks was never gated through
         the real seam. This file closes that gap (the sole remaining
         e2e-coverage hole identified in the coverage review; runbook §3
         session_end item). The existing mocked session_end tests are KEPT —
         they pin the cleanup-reaper / pause-vs-review / PR-state behavior;
         this file adds the one missing real-resolver consumption pin.

Used by: the pact-plugin test suite (caller-4 inert-class regression guard).

================================ ANTI-MOCK INVARIANT ===========================
get_task_list / iter_team_task_jsons are NOT stubbed — the real
get_team_name -> team-dir -> glob resolution IS the seam under test (driven via
a Path.home redirect + a real team-named task dir, the same pattern as the
missed-wake integration centerpiece). The ONLY test double is check_pr_state —
an EXTERNAL `gh` CLI call, NOT the resolver — stubbed for determinism so a
found PR yields the warning without a network round-trip. Stubbing the external
gh dependency does not undermine the seam guarantee; stubbing get_task_list
would (and is forbidden here).

============================ NON-VACUITY (source-revert) =======================
Method: in-place `git checkout <fix-sha>^ -- hooks/shared/task_utils.py` on a
clean tree, OR — when task_utils.py has concurrent uncommitted WIP (e.g. a
parallel remediation) — an ISOLATED throwaway worktree
(`git worktree add --detach /tmp/v <fix-sha>^`; cp this file in; run; then
`git worktree remove --force`) so the shared tree is never mutated.

Pre-fix behavior: the arg-less session_end.get_task_list() reads
~/.claude/tasks/{session_id}/ (absent in a team session) -> None -> EVERY test
here breaks on its `assert tasks is not None` precondition (and the two
*_non_vacuity_gate tests additionally lose their warning assertion, since
check_unpaused_pr's `if not pr_number and tasks:` task-scan is skipped on
None). No TypeError artifact — get_task_list is arg-less and exists pre-fix.

Expected cardinality: FULL FILE pre-fix -> {3 failed} (all three are coupled to
the resolver via the `assert tasks is not None` precondition); restore the fix
-> {3 passed}. `pytest -k non_vacuity_gate` -> {2 failed} pre-fix / {2 passed}
post-fix (the 2 named gate tests). The discrimination control
(test_team_task_without_pr_indicator_no_warning) is ALSO coupled to the resolver
(so it too fails pre-fix), but it is NOT named a *gate* because its load-bearing
assertion is a POST-fix property — "a resolved team task carrying no PR
indicator yields no warning" — which only has meaning once the resolver returns
the task. Empirically confirmed via isolated worktree @ <fix-sha>^: {3 failed}.
================================================================================
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import session_end  # noqa: E402

TEAM = "pact-testteam"
SID = "aaaaaaaa-1111-2222-3333-444444444444"
PROJECT_DIR = "/test/project"


@pytest.fixture
def session_end_live_env(tmp_path, monkeypatch, pact_context):
    """Path.home -> tmp + a real PACT team context, NO resolver stub. Returns
    the real team-named task dir; tests drop task JSON there to drive the real
    get_team_name -> team-dir resolver that session_end.get_task_list() reads.
    No journal is written, so read_events('session_consolidated' / 'session_paused'
    / 'review_dispatch') all return [] -> the review-event pr_number path yields
    nothing -> check_unpaused_pr's task-scan fallback runs (the path under test)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id=SID, project_dir=PROJECT_DIR)
    team_dir = tmp_path / ".claude" / "tasks" / TEAM
    team_dir.mkdir(parents=True)
    return team_dir


def _write_task(team_dir: Path, task_id: str, metadata: dict) -> None:
    (team_dir / f"{task_id}.json").write_text(json.dumps({
        "id": task_id, "owner": "x", "subject": "s",
        "status": "in_progress", "metadata": metadata,
    }), encoding="utf-8")


class TestSessionEndUnpausedPrRealResolver:
    """check_unpaused_pr's task-scan fallback driven through the REAL
    get_task_list resolver. The warning is produced ONLY when the scan finds a
    PR indicator in a task that the real team-dir resolver returned."""

    def test_non_vacuity_gate_pr_number_in_task_metadata(self, session_end_live_env, monkeypatch):
        # Real team task carrying metadata.pr_number -> the task-scan fallback
        # must find it THROUGH the real resolver and warn. Pre-fix: get_task_list
        # -> None -> no scan -> no warning -> FAIL.
        _write_task(session_end_live_env, "1", {"pr_number": 4242})
        monkeypatch.setattr(session_end, "check_pr_state", lambda pr: "")  # external gh stub
        tasks = session_end.get_task_list()  # REAL resolver, arg-less, NO stub
        assert tasks is not None, "real resolver must return the team task (the seam under test)"
        warning = session_end.check_unpaused_pr(tasks=tasks, project_slug="proj")
        assert warning is not None and "4242" in warning, (
            "the task-scan fallback must resolve the pr_number through the real "
            "team-dir resolver and emit the unpaused-PR warning"
        )

    def test_non_vacuity_gate_pr_url_in_handoff(self, session_end_live_env, monkeypatch):
        # The OTHER task-scan sub-path: a github PR URL embedded in a handoff
        # string value (regex scan). Also driven through the real resolver.
        _write_task(session_end_live_env, "1", {
            "handoff": {"produced": "landed via https://github.com/org/repo/pull/777 today"},
        })
        monkeypatch.setattr(session_end, "check_pr_state", lambda pr: "")
        tasks = session_end.get_task_list()
        assert tasks is not None
        warning = session_end.check_unpaused_pr(tasks=tasks, project_slug="proj")
        assert warning is not None and "777" in warning, (
            "the handoff-URL scan sub-path must resolve the PR through the real resolver"
        )

    def test_team_task_without_pr_indicator_no_warning(self, session_end_live_env, monkeypatch):
        # Discrimination control: a real team task with NO pr indicator -> the
        # scan finds nothing -> None. Proves the warnings above come SPECIFICALLY
        # from the PR in the task metadata, not from the mere fact that the
        # resolver returned tasks. NOT named a *gate* because its load-bearing
        # assertion (no-PR-task -> no-warning) is a POST-fix property; but it is
        # still resolver-coupled via the `assert tasks is not None` precondition,
        # so it too FAILS on a source-revert (full-file pre-fix = {3 failed}).
        _write_task(session_end_live_env, "1", {"intentional_wait": {"reason": "x"}})
        monkeypatch.setattr(session_end, "check_pr_state", lambda pr: "")
        tasks = session_end.get_task_list()
        assert tasks is not None, "resolver returns the task; the scan just finds no PR in it"
        assert session_end.check_unpaused_pr(tasks=tasks, project_slug="proj") is None, (
            "a team task with no PR indicator must NOT produce a warning"
        )
