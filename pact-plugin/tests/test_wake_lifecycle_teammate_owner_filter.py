"""
Teammate-owner filter invariants for
pact-plugin/hooks/shared/wake_lifecycle.py::_lifecycle_relevant.

Pins the step-4 owner-classification check that excludes unowned umbrella
tasks, orphan-owner tasks, and team-lead-owned tasks from the wake-
mechanism's active-tally. Direct-import tests reuse the fixture pattern
from test_inbox_wake_lifecycle_helper.py (write a team config + task
under tmp_path, monkeypatch Path.home).

Counter-test-by-revert expected cardinality
-------------------------------------------
Reverting ONLY the step-4 owner-check block in `_lifecycle_relevant`
(keep the new helpers `_owner_is_team_member`, `_is_lead_owned`, the
`shared.pact_context` imports, and the docstring updates) produces
**8 failures** under the current test surface:

  - 3 unit cases in this file:
      * test_unowned_umbrella_task_excluded
      * test_lead_owned_task_excluded
      * test_orphan_owner_excluded
    (test_teammate_owned_task_counted passes either way — teammate
    baseline always counts; test_config_unreadable_fail_conservative
    also passes either way — fail-conservative result matches pre-fix
    "count any owner" default.)

  - 1 behavioral umbrella case in this file:
      * test_umbrella_scenario_one_to_zero_transition
    (pre-fix: count_active_tasks returns 2→1; post-fix: 1→0).

  - 3 inverted-in-lockstep legacy cases in
    test_inbox_wake_lifecycle_helper.py (assertions flipped from the
    pre-fix orphan-counted behavior to the post-fix orphan-excluded
    behavior in the same commit as the predicate change):
      * test_lifecycle_relevant_owner_named_secretary_without_agenttype_excluded_as_orphan
      * test_count_active_tasks_skips_signal_and_orphans
      * test_count_active_tasks_secretary_owner_without_agenttype_excluded_as_orphan

  - 1 structural-invariant case in test_inbox_wake_lifecycle_helper.py:
      * test_lifecycle_relevant_preserves_fail_conservative_audit_anchor
    (the inline §5.2-derived comment block at step 4 is removed when
    step 4 is reverted; pin catches the audit-anchor regression).

  Tests that pass either way on pure step-4 revert and are NOT counted:
      * test_teammate_owned_task_counted (baseline; passes pre and post)
      * test_config_unreadable_fail_conservative (passes pre and post —
        pre-fix default also returns True for any owner with empty
        members list)
      * test_helper_imports_pact_context_for_owner_filter (helpers and
        imports remain on pure step-4 revert)
      * test_lifecycle_relevant_documents_orphan_exclusion (docstring
        is not part of the step-4 inline block)

Audit-anchor: when extending or refactoring this file, preserve the
3-unit + 1-behavioral + 3-legacy-lockstep + 1-audit-anchor cardinality
so the counter-test-by-revert delta remains computable. If you add new
unit cases that flip on pure step-4 revert, update the count comment
above in lockstep.
"""

import json
import sys
from pathlib import Path

import pytest

# Hooks dir is added to sys.path by conftest.
import shared.wake_lifecycle as wl
from shared.wake_lifecycle import count_active_tasks


def _write_team_config(tmp_path, team_name, members, lead_agent_id=""):
    """Write a team config under tmp_path/.claude/teams/<team_name>/config.json.

    Optional `lead_agent_id` populates the top-level `leadAgentId` field
    the way the live Agent Teams platform records it at TeamCreate (see
    ~/.claude/teams/<team>/config.json on disk).
    """
    team_dir = tmp_path / ".claude" / "teams" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "team_name": team_name,
        "members": members,
    }
    if lead_agent_id:
        config["leadAgentId"] = lead_agent_id
    (team_dir / "config.json").write_text(
        json.dumps(config),
        encoding="utf-8",
    )


def _stage_task(tmp_path, team, task_id, **fields):
    d = tmp_path / ".claude" / "tasks" / team
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task_id}.json").write_text(
        json.dumps({"id": task_id, **fields}), encoding="utf-8"
    )


# ---------- 5 unit cases for the edge table ----------


def test_unowned_umbrella_task_excluded(tmp_path, monkeypatch):
    """An umbrella task created by a workflow command (no owner field)
    does not count toward the wake tally. This is the core scenario the
    fix addresses: /PACT:orchestrate, /PACT:comPACT, /PACT:peer-review
    create feature/phase records with no owner; they were keeping the
    cron-based pending-scan armed indefinitely."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-umbrella"
    _write_team_config(
        tmp_path, team,
        [
            {"name": "team-lead", "agentId": "team-lead@T", "agentType": "pact-orchestrator"},
            {"name": "architect", "agentId": "architect@T", "agentType": "pact-architect"},
        ],
        lead_agent_id="team-lead@T",
    )
    task_no_owner = {"status": "in_progress"}
    task_empty_owner = {"status": "in_progress", "owner": ""}
    assert wl._lifecycle_relevant(task_no_owner, team) is False
    assert wl._lifecycle_relevant(task_empty_owner, team) is False


def test_lead_owned_task_excluded(tmp_path, monkeypatch):
    """A task owned by the team-lead (whose member.agentId equals the
    team config's leadAgentId) does not count toward the wake tally.
    The lead's own work does not arm the lead's wake mechanism — the
    lead is the consumer, not the producer."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-lead-task"
    _write_team_config(
        tmp_path, team,
        [
            {"name": "team-lead", "agentId": "team-lead@T", "agentType": "pact-orchestrator"},
            {"name": "architect", "agentId": "architect@T", "agentType": "pact-architect"},
        ],
        lead_agent_id="team-lead@T",
    )
    task = {"status": "in_progress", "owner": "team-lead"}
    assert wl._lifecycle_relevant(task, team) is False


def test_teammate_owned_task_counted(tmp_path, monkeypatch):
    """A task owned by a non-lead team member counts toward the wake
    tally. Baseline behavior — this is the case the wake mechanism
    exists to surface. Passes either pre- or post-fix; included for
    edge-table completeness."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-teammate"
    _write_team_config(
        tmp_path, team,
        [
            {"name": "team-lead", "agentId": "team-lead@T", "agentType": "pact-orchestrator"},
            {"name": "architect", "agentId": "architect@T", "agentType": "pact-architect"},
        ],
        lead_agent_id="team-lead@T",
    )
    task = {"status": "in_progress", "owner": "architect"}
    assert wl._lifecycle_relevant(task, team) is True


def test_orphan_owner_excluded(tmp_path, monkeypatch):
    """An owner string that doesn't match any current team member is an
    orphan owner (stale shutdown-mid-workflow owner, spoofed name, etc.)
    and does not count toward the wake tally. Members list is non-empty
    so the fail-CONSERVATIVE branch does not apply."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-orphan"
    _write_team_config(
        tmp_path, team,
        [
            {"name": "team-lead", "agentId": "team-lead@T", "agentType": "pact-orchestrator"},
            {"name": "architect", "agentId": "architect@T", "agentType": "pact-architect"},
        ],
        lead_agent_id="team-lead@T",
    )
    task = {"status": "in_progress", "owner": "ghost"}
    assert wl._lifecycle_relevant(task, team) is False


def test_config_unreadable_fail_conservative(tmp_path, monkeypatch):
    """When the team config is unreadable (no config file on disk;
    `_iter_members` returns []), the owner-check short-circuits and the
    task is counted. Under-arm (silent teardown loss while teammate work
    is in flight) is unrecoverable; over-arm (extra empty scans) is
    recoverable on the next state change — so the wake mechanism fails
    toward counting.

    Passes either pre- or post-fix; included for edge-table completeness
    AND to pin the fail-CONSERVATIVE posture so future cleanup cannot
    silently invert it to fail-CLOSED."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # No team config written — members list will be [].
    task = {"status": "in_progress", "owner": "some-owner"}
    assert wl._lifecycle_relevant(task, "team-no-config") is True


# ---------- Behavioral umbrella scenario ----------


def test_umbrella_scenario_one_to_zero_transition(tmp_path, monkeypatch):
    """End-to-end reproduction of the bug fix.

    Setup: a team with one umbrella task (no owner) and one teammate-
    owned active task. Pre-fix, `count_active_tasks` returns 2→1 when
    the teammate task completes; the 1→0 transition that
    `wake_lifecycle_emitter._decide_directive` watches for never fires,
    so the cron-based pending-scan stays armed indefinitely.

    Post-fix, the umbrella task is excluded from the count: 1→0 is
    reached on teammate completion, and the wake mechanism tears down
    correctly. This is the load-bearing assertion of the fix."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-umbrella-scenario"
    _write_team_config(
        tmp_path, team,
        [
            {"name": "team-lead", "agentId": "team-lead@T", "agentType": "pact-orchestrator"},
            {"name": "architect", "agentId": "architect@T", "agentType": "pact-architect"},
        ],
        lead_agent_id="team-lead@T",
    )
    # Task 1: unowned umbrella (created by a workflow command).
    _stage_task(tmp_path, team, "1", status="in_progress")
    # Task 2: teammate-owned active work.
    _stage_task(tmp_path, team, "2", status="in_progress", owner="architect")

    # Pre-fix: 2 (umbrella + teammate). Post-fix: 1 (only teammate).
    assert count_active_tasks(team) == 1, (
        "Umbrella task must not count toward the wake tally."
    )

    # Transition Task 2 to completed (teammate finishes work).
    _stage_task(tmp_path, team, "2", status="completed", owner="architect")

    # Pre-fix: 1 (umbrella remains; never reaches 0). Post-fix: 0.
    assert count_active_tasks(team) == 0, (
        "1→0 transition must fire after the last teammate task completes; "
        "the umbrella task must not block teardown."
    )


# ---------- Helper-level pure-never-raises invariants ----------


def test_owner_is_team_member_pure_never_raises(tmp_path, monkeypatch):
    """`_owner_is_team_member` is pure and never raises on any shape of
    owner or team_name."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for owner in (None, "", 42, [], {}, ["x"], True):
        try:
            result = wl._owner_is_team_member(owner, "any-team")
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"_owner_is_team_member raised on owner={owner!r}: {exc}")
        assert isinstance(result, bool)
    for team_name in (None, "", 42, []):
        try:
            result = wl._owner_is_team_member("x", team_name)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_owner_is_team_member raised on team_name={team_name!r}: {exc}"
            )
        assert isinstance(result, bool)


def test_is_lead_owned_pure_never_raises(tmp_path, monkeypatch):
    """`_is_lead_owned` is pure and never raises on any shape of owner
    or team_name."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for owner in (None, "", 42, [], {}, ["x"], True):
        try:
            result = wl._is_lead_owned(owner, "any-team")
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"_is_lead_owned raised on owner={owner!r}: {exc}")
        assert isinstance(result, bool)
    for team_name in (None, "", 42, []):
        try:
            result = wl._is_lead_owned("x", team_name)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_is_lead_owned raised on team_name={team_name!r}: {exc}"
            )
        assert isinstance(result, bool)


def test_is_lead_owned_requires_both_name_and_agentid_match(tmp_path, monkeypatch):
    """A member whose `name` matches the owner but whose `agentId` does
    NOT match `leadAgentId` is NOT lead-owned. Conversely, a member
    whose `agentId` matches `leadAgentId` but whose `name` does NOT
    match the owner is also NOT lead-owned. Both conditions are
    required — pins the AND-composition in `_is_lead_owned`."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-and-comp"
    _write_team_config(
        tmp_path, team,
        [
            {"name": "team-lead", "agentId": "team-lead@T"},
            {"name": "architect", "agentId": "architect@T"},
        ],
        lead_agent_id="team-lead@T",
    )
    # name matches lead, agentId matches lead → True.
    assert wl._is_lead_owned("team-lead", team) is True
    # name does not match (architect is not lead) → False.
    assert wl._is_lead_owned("architect", team) is False
    # owner not in members at all → False.
    assert wl._is_lead_owned("ghost", team) is False
