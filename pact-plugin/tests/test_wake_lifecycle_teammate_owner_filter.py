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
Two revert protocols apply to this module. Each strips a different
amount of the owner-classification implementation and produces a
different empirical failure cardinality. A future reviewer following
either protocol should reproduce the documented number exactly; a
divergence indicates either a coverage drift or an upstream refactor
that this docstring did not anticipate (file a follow-up issue rather
than silently adjusting the count).

Protocol 1 — body-only revert (default protocol; 17 fail)
---------------------------------------------------------
Delete ONLY the step-4 owner-check executable block in
`_lifecycle_relevant`. Keep `_OwnerClassification`, `_classify_owner`,
`_owner_is_known_team_member`, `_is_lead_owned`,
`_warn_empty_team_config_once`, the `shared.pact_context` imports, and
the `_lifecycle_relevant` docstring carve-out.

Byte-precise procedure (reproduces 17 fail / 71 pass / 2 skip):

  1. cp pact-plugin/hooks/shared/wake_lifecycle.py /tmp/wl.bak
  2. Locate the step-4 body via `grep -n "if classification.config_readable:"
     pact-plugin/hooks/shared/wake_lifecycle.py`. The block extends
     from that line through the `_warn_empty_team_config_once(team_name)`
     call inside the `elif team_name:` branch. The full delete range
     covers the `if classification.config_readable:` branch (3 owner-
     shape / membership / lead-owned guards) PLUS the `elif team_name:`
     branch including the inline Fail-CONSERVATIVE / under-arm /
     unrecoverable audit-anchor comment block AND the
     `_warn_empty_team_config_once(team_name)` call. Replace the
     entire deleted block with a single blank line so the `# Step 5`
     comment and `metadata = task.get(...)` statement that follow
     remain correctly indented.
  3. KEEP (do NOT delete under Protocol 1): module-top imports of
     `_iter_members` and `_read_team_lead_agent_id`; the
     `_OwnerClassification` dataclass; `_classify_owner`;
     `_owner_is_known_team_member`; `_is_lead_owned`;
     `_warn_empty_team_config_once`; the `_lifecycle_relevant`
     docstring carve-out; and the step-4 lead-in comment paragraph
     (the `# Step 4: teammate-owner check. ...` block immediately
     above the executable body).
  4. find pact-plugin -name __pycache__ -type d -exec rm -rf {} +
  5. pytest pact-plugin/tests/test_wake_lifecycle_teammate_owner_filter.py
       pact-plugin/tests/test_inbox_wake_lifecycle_helper.py --no-header
  6. Expect 17 failed, 71 passed, 2 skipped.
  7. cp /tmp/wl.bak pact-plugin/hooks/shared/wake_lifecycle.py
  8. git diff --quiet pact-plugin/hooks/shared/wake_lifecycle.py
     (should exit 0; byte-identity restored)

Breakdown of the 17 body-only failures
--------------------------------------
  13 new-file failures (this file):
    Core edge-table cases (4):
    - test_unowned_umbrella_task_excluded
    - test_lead_owned_task_excluded
    - test_orphan_owner_excluded
    - test_umbrella_scenario_one_to_zero_transition
      (without the owner check: count_active_tasks returns 2→1; with
      it: 1→0)

    Adversarial-edge owner-shape cases (5 parametrize cells):
    - test_stringified_non_member_owners_excluded_as_orphan[42]
    - test_stringified_non_member_owners_excluded_as_orphan[True]
    - test_stringified_non_member_owners_excluded_as_orphan[[]]
    - test_stringified_non_member_owners_excluded_as_orphan[None]
    - test_stringified_non_member_owners_excluded_as_orphan[0]

    Adversarial-edge fixture and call-site cases (2):
    - test_member_entry_missing_or_falsy_name_does_not_match
    - test_lead_owner_short_circuit_does_not_fire_on_name_only_lead_agentid_match

    Integration and stress cases (2):
    - test_compact_shaped_layout_drops_to_zero_after_teammate_completion
    - test_count_active_tasks_scales_linearly_on_large_team

  3 legacy lockstep failures (test_inbox_wake_lifecycle_helper.py):
    Assertions inverted from the pre-owner-check orphan-counted
    behavior to the post-owner-check orphan-excluded behavior.
    - test_lifecycle_relevant_owner_named_secretary_without_agenttype_excluded_as_orphan
    - test_count_active_tasks_skips_signal_and_orphans
    - test_count_active_tasks_secretary_owner_without_agenttype_excluded_as_orphan

  1 structural audit-anchor failure (test_inbox_wake_lifecycle_helper.py):
    The fail-CONSERVATIVE / under-arm / unrecoverable phrases live
    inside the step-4 `elif team_name:` branch. A body-only revert
    deletes the audit-anchor along with the executable block; this
    test flips as a TRUE counter-signal (not a phantom-green
    source-text pin elsewhere in the file).
    - test_lifecycle_relevant_preserves_fail_conservative_audit_anchor

Protocol 2 — full-section revert (broader protocol; 30 fail + 4 errors)
-----------------------------------------------------------------------
Replace the entire owner-classification surface with the pre-fix
shape. Run `git checkout main --
pact-plugin/hooks/shared/wake_lifecycle.py
pact-plugin/hooks/shared/pact_context.py`, then run pytest as in
Protocol 1 step 5.

Expect 30 failed, 4 errors, 66 passed, 2 skipped (34 broken total).
After measuring, restore via `cp` from the backup and
`git reset HEAD --` on both files to drop the index drift left by
the `git checkout main --` operation.

The 34 = 17 body-only failures (above) + 13 additional flips driven
by the helpers / dataclass / observability function disappearing.
The 13 additional flips ride on `AttributeError` rather than test-
assertion failures (the test imports a helper name that no longer
exists on the module). They split between FAILs (assertion never
reached because the body errored out) and ERRORs (fixture setup
errors before the test body runs):

  Helper-wrapper FAILs (3 — body-AttributeError on lookup):
  - test_owner_is_known_team_member_pure_never_raises
  - test_is_lead_owned_pure_never_raises
  - test_is_lead_owned_requires_both_name_and_agentid_match

  Helper-wrapper FAILs (2 — body-AttributeError on `_is_lead_owned`):
  - test_empty_or_missing_leadagentid_does_not_misclassify_lead[]
  - test_empty_or_missing_leadagentid_does_not_misclassify_lead[None]

  Dataclass / projection FAILs (8 — direct `_classify_owner` calls):
  - test_classify_owner_empty_team_name_returns_all_false
  - test_classify_owner_non_string_team_name_returns_all_false
  - test_classify_owner_empty_members_marks_config_unreadable
  - test_classify_owner_readable_config_with_bad_owner_marks_orphan
  - test_classify_owner_readable_config_no_name_match_marks_orphan
  - test_classify_owner_teammate_match_marks_known_not_lead
  - test_classify_owner_lead_match_marks_lead
  - test_classify_owner_member_without_agenttype_yields_none_agent_type

  Dedupe-fixture ERRORs (4 — fixture setup AttributeError on
  `_EMPTY_CONFIG_WARN_TEAMS` module attribute):
  - test_warn_empty_team_config_once_first_call_writes
  - test_warn_empty_team_config_once_repeat_call_is_noop
  - test_warn_empty_team_config_once_per_team_isolation
  - test_warn_empty_team_config_once_rejects_bad_team_name

These 13 tests are baseline pins under Protocol 1 (they pass either
way because the helpers / dataclass / observability function still
exist), but they ARE counter-signal under Protocol 2 (they fail when
the entire owner-classification surface is removed). Two-protocol
classification gives a fresh reader a precise reading of what each
test pins: behavior of the step-4 executable block (Protocol 1
flippers), helper-surface existence (Protocol 2-only flippers), or
true baseline (passes under both).

Tests that pass under BOTH protocols (true baselines; NOT counter-
signal under either revert):
    - test_teammate_owned_task_counted
        Baseline: a known teammate-owned task counts regardless of
        whether the owner-check ever fired. Pre-owner-check, every
        task with an owner counted (orphans included); post-owner-
        check, only known teammates count. The teammate case sits in
        the intersection. Pin against future inversion.
    - test_config_unreadable_fail_conservative
        Baseline: the wake mechanism's purpose is to surface teammate
        work, and an unreadable config must NOT silently teardown
        active sessions. Pre-owner-check, every task counted because
        the carve-out did not exist; post-owner-check, the empty-
        members fall-through preserves that posture explicitly. The
        test pins the fall-through as a regression guard against any
        future cleanup that inverts the unreadable-config branch to
        fail-CLOSED.

Genesis of the 17 number
------------------------
An earlier architecture-spec draft (gitignored, not in the repo)
projected 5 fail on revert. The next iteration super-sided it to 8
after a legacy-test lockstep update brought the legacy file into the
same revert envelope. A later iteration super-sided it to 17 after
adding adversarial-edge, integration, and stress coverage:
17 = 4 core + 9 adversarial-edge + 3 legacy lockstep + 1 audit-anchor.
The current iteration adds 12 helper-surface / dataclass-shape /
observability tests that are Protocol 2-only flippers (not Protocol 1
flippers, by design). The Protocol 1 count remains 17; the Protocol 2
count grows from 22 broken to 34 broken (30 fail + 4 errors).

When extending this file, preserve the Protocol 1 cardinality of 17
and the Protocol 2 cardinality of 34 broken (30 fail + 4 errors) so
the counter-test-by-revert delta remains computable. If you add new
unit cases that flip under Protocol 1, update the Protocol 1 count
above in lockstep; if you add new helper-pinning tests that flip
ONLY under Protocol 2, update the Protocol 2 count (and decide
whether the new test will fail-by-body-AttributeError or
error-by-fixture-AttributeError — both shapes are legitimate). Source-
text pins for non-load-bearing artifacts (an import line, a docstring
phrase outside the executable body) are deliberately omitted — they
are phantom-green-by-absence under Protocol 1 and provide no counter-
signal beyond what the runtime tests already cover.
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


def test_owner_is_known_team_member_pure_never_raises(tmp_path, monkeypatch):
    """`_owner_is_known_team_member` is pure and never raises on any shape of
    owner or team_name."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for owner in (None, "", 42, [], {}, ["x"], True):
        try:
            result = wl._owner_is_known_team_member(owner, "any-team")
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"_owner_is_known_team_member raised on owner={owner!r}: {exc}")
        assert isinstance(result, bool)
    for team_name in (None, "", 42, []):
        try:
            result = wl._owner_is_known_team_member("x", team_name)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"_owner_is_known_team_member raised on team_name={team_name!r}: {exc}"
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
    required — pins the AND-composition in `_is_lead_owned`.

    The agentId-matches-but-name-doesn't direction is exercised by a
    standalone fixture where the team config holds a single member whose
    `agentId` equals `leadAgentId` but whose `name` differs from the
    queried owner. A correct AND-composition returns False; an OR-bug
    or name-stripped variant would return True."""
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

    # Inverse direction: agentId matches leadAgentId but the queried owner
    # name does not match any member name. An OR-bug or name-stripped
    # check would treat this as lead-owned; the AND-composition rejects.
    team_inv = "team-and-comp-inverse"
    _write_team_config(
        tmp_path, team_inv,
        [
            # Lone member's agentId IS leadAgentId, but name='renamed-lead'
            # — querying owner='team-lead' must NOT match.
            {"name": "renamed-lead", "agentId": "team-lead@T"},
        ],
        lead_agent_id="team-lead@T",
    )
    assert wl._is_lead_owned("team-lead", team_inv) is False, (
        "Lead-ownership requires BOTH name AND agentId match. An owner "
        "string that doesn't match any member name must return False even "
        "if some member's agentId equals leadAgentId."
    )


# ---------- Adversarial-edge: owner type-coercion at the call site ----------


@pytest.mark.parametrize("stringified_owner", ["42", "True", "[]", "None", "0"])
def test_stringified_non_member_owners_excluded_as_orphan(
    stringified_owner, tmp_path, monkeypatch
):
    """An owner that is a *string* representation of a non-string value
    (e.g., the literal text '42' or 'True') is treated as any other
    string: if it doesn't match a member's `name`, it's an orphan and
    excluded. Pins behavior under accidental type-coercion at upstream
    write sites — a coder who writes `owner=str(value)` without
    validating gets orphan-excluded, not phantom-counted."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-stringified-owners"
    _write_team_config(
        tmp_path, team,
        [
            {"name": "team-lead", "agentId": "team-lead@T"},
            {"name": "architect", "agentId": "architect@T"},
        ],
        lead_agent_id="team-lead@T",
    )
    task = {"status": "in_progress", "owner": stringified_owner}
    assert wl._lifecycle_relevant(task, team) is False, (
        f"Stringified non-member owner {stringified_owner!r} must be "
        "treated as orphan and excluded."
    )


# ---------- Adversarial-edge: malformed member entries ----------


def test_member_entry_missing_or_falsy_name_does_not_match(tmp_path, monkeypatch):
    """Member entries lacking a `name` field, or with `name=None` or
    `name=""`, must not accidentally match any owner. The membership
    check uses `member.get("name") == owner` which would return True if
    BOTH sides equal `None` — but a real owner string can never be
    `None`, and an empty owner is rejected at the helper's first guard.
    This test pins the boundary so a future refactor that loosens the
    owner-shape guard does not let a missing-name member act as a
    wildcard match."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-malformed-members"
    _write_team_config(
        tmp_path, team,
        [
            # Three malformed entries — none has a real string `name`.
            {"agentId": "ghost-1@T"},          # name field absent
            {"name": None, "agentId": "ghost-2@T"},
            {"name": "", "agentId": "ghost-3@T"},
            # One legit teammate so members list is non-empty.
            {"name": "architect", "agentId": "architect@T"},
        ],
        lead_agent_id="team-lead@T",
    )
    # Owner that would only "match" a missing-name member via a None
    # equality accident — must remain orphan.
    task = {"status": "in_progress", "owner": "any-name"}
    assert wl._lifecycle_relevant(task, team) is False
    # Legit teammate still resolves correctly — the malformed sibling
    # entries do not break iteration.
    task_legit = {"status": "in_progress", "owner": "architect"}
    assert wl._lifecycle_relevant(task_legit, team) is True


# ---------- Adversarial-edge: leadAgentId edge values ----------


@pytest.mark.parametrize("lead_value", ["", None])
def test_empty_or_missing_leadagentid_does_not_misclassify_lead(
    lead_value, tmp_path, monkeypatch
):
    """When the team config has `leadAgentId` field present but empty
    string or `null`, `_is_lead_owned` must return False for every
    owner. The lead-owner short-circuit must not fire incorrectly
    against a sentinel-or-missing leadAgentId — a teammate-owned task
    in such a team config must still count, not be misclassified as
    lead-owned."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-empty-lead"
    team_dir = tmp_path / ".claude" / "teams" / team
    team_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "team_name": team,
        "members": [
            {"name": "architect", "agentId": "architect@T"},
        ],
        "leadAgentId": lead_value,
    }
    (team_dir / "config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    # No owner is lead-owned when leadAgentId is empty/null.
    assert wl._is_lead_owned("architect", team) is False
    # Teammate task still counts toward the tally.
    task = {"status": "in_progress", "owner": "architect"}
    assert wl._lifecycle_relevant(task, team) is True


# ---------- Adversarial-edge: AND-composition (call-site path) ----------


def test_lead_owner_short_circuit_does_not_fire_on_name_only_lead_agentid_match(
    tmp_path, monkeypatch
):
    """A member whose `agentId` equals `leadAgentId` but whose `name`
    does NOT match the task owner must NOT trigger the lead-owner
    short-circuit at the predicate call site. The task should count
    (subject to the orphan check — see fixture). This exercises the
    AND-composition through `_lifecycle_relevant` rather than the
    isolated helper, closing the call-site path the helper-level test
    only validates structurally."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-name-only-agentid-match"
    _write_team_config(
        tmp_path, team,
        [
            # `architect`'s agentId happens to equal leadAgentId — but
            # the owner field on the task is `architect`, not the
            # member whose name matches the lead role.
            {"name": "architect", "agentId": "team-lead@T"},
            {"name": "team-lead", "agentId": "some-other-id@T"},
        ],
        lead_agent_id="team-lead@T",
    )
    task = {"status": "in_progress", "owner": "architect"}
    # `architect` IS a team member AND `architect`'s agentId IS
    # leadAgentId — under the implemented AND-composition, _is_lead_owned
    # returns True (both name='architect' AND agentId==leadAgentId match
    # on the same member). The task IS treated as lead-owned and
    # excluded.
    assert wl._is_lead_owned("architect", team) is True
    assert wl._lifecycle_relevant(task, team) is False
    # The legitimate-named lead, however, is NOT lead-owned here because
    # its member's agentId does NOT equal leadAgentId — so a task owned
    # by "team-lead" counts in this misconfigured team. The pin
    # documents the AND-composition semantics: lead-ownership is the
    # CONJUNCTION of "name in members" AND "that same member's agentId
    # equals leadAgentId," and a member with a misaligned agentId is
    # not promoted to lead even by name.
    task_named_lead = {"status": "in_progress", "owner": "team-lead"}
    assert wl._is_lead_owned("team-lead", team) is False
    assert wl._lifecycle_relevant(task_named_lead, team) is True


# ---------- Integration: realistic comPACT-shaped task layout ----------


def test_compact_shaped_layout_drops_to_zero_after_teammate_completion(
    tmp_path, monkeypatch
):
    """End-to-end integration over a realistic comPACT layout:
    1 unowned feature task (the umbrella), 2 teammate-claimed work
    tasks, 1 secretary signal task (algedonic). Before any completion
    the count is 2 (only the two teammate work tasks; umbrella excluded
    by the owner check, signal excluded by the signal carve-out). As
    each teammate task completes the count walks down 2→1→0, reaching
    the 1→0 transition that `wake_lifecycle_emitter._decide_directive`
    listens for. The umbrella never blocks teardown."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-compact-integration"
    _write_team_config(
        tmp_path, team,
        [
            {"name": "team-lead", "agentId": "team-lead@T", "agentType": "pact-orchestrator"},
            {"name": "backend-coder", "agentId": "backend-coder@T", "agentType": "pact-backend-coder"},
            {"name": "test-engineer", "agentId": "test-engineer@T", "agentType": "pact-test-engineer"},
            {"name": "session-secretary", "agentId": "session-secretary@T", "agentType": "pact-secretary"},
        ],
        lead_agent_id="team-lead@T",
    )
    # Unowned feature task (created by /PACT:comPACT).
    _stage_task(tmp_path, team, "feature", status="in_progress")
    # Two teammate-claimed work tasks.
    _stage_task(tmp_path, team, "code-work", status="in_progress", owner="backend-coder")
    _stage_task(tmp_path, team, "test-work", status="in_progress", owner="test-engineer")
    # Secretary signal task (algedonic) — owned by secretary but
    # carved out by the signal-task metadata check at step 6.
    _stage_task(
        tmp_path, team, "alert",
        status="in_progress", owner="session-secretary",
        metadata={"completion_type": "signal", "type": "algedonic"},
    )

    assert count_active_tasks(team) == 2, (
        "Pre-completion: only the two teammate work tasks count. "
        "Umbrella excluded by owner check; signal excluded by metadata "
        "carve-out."
    )
    # First teammate completes.
    _stage_task(tmp_path, team, "code-work", status="completed", owner="backend-coder")
    assert count_active_tasks(team) == 1
    # Last teammate completes — the 1→0 transition.
    _stage_task(tmp_path, team, "test-work", status="completed", owner="test-engineer")
    assert count_active_tasks(team) == 0, (
        "Post-last-teammate-completion: count must reach 0 so the "
        "wake-mechanism teardown can fire. Umbrella and signal must "
        "stay out of the tally."
    )


# ---------- Performance: bounded constant factor, no quadratic blowup ----------


def test_count_active_tasks_scales_linearly_on_large_team(tmp_path, monkeypatch):
    """Stress test: 100 tasks across a team with 20 members, mixed
    owners (lead-owned umbrella tasks, multiple teammates, orphans,
    signals). `count_active_tasks` must complete quickly — each
    `_lifecycle_relevant` invocation invokes `_iter_members` a bounded
    constant number of times (currently up to 3 in this module plus
    1 from `_is_wake_excluded_agent_type`); the aggregate is
    O(tasks × members) = O(N×M), unchanged in big-O from pre-fix.

    The wall-clock budget here is intentionally generous — the goal is
    to catch a future regression that introduces a per-task O(M²) or
    O(N×M²) shape (e.g., a nested member iteration), not to pin
    micro-performance. On a developer laptop the typical runtime is
    well under 100 ms; we allow 5 s to keep CI noise from flaking."""
    import time

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-stress"
    members = [
        {"name": "team-lead", "agentId": "team-lead@T", "agentType": "pact-orchestrator"},
    ]
    for i in range(19):
        members.append(
            {"name": f"teammate-{i}", "agentId": f"teammate-{i}@T",
             "agentType": "pact-backend-coder"}
        )
    _write_team_config(tmp_path, team, members, lead_agent_id="team-lead@T")

    # 100 tasks: 30 unowned umbrellas, 50 teammate-owned active,
    # 10 orphan-owned, 5 lead-owned, 5 signal-tasks.
    for i in range(30):
        _stage_task(tmp_path, team, f"umbrella-{i}", status="in_progress")
    for i in range(50):
        owner = f"teammate-{i % 19}"
        _stage_task(tmp_path, team, f"work-{i}", status="in_progress", owner=owner)
    for i in range(10):
        _stage_task(tmp_path, team, f"orphan-{i}", status="in_progress",
                    owner=f"ghost-{i}")
    for i in range(5):
        _stage_task(tmp_path, team, f"lead-{i}", status="in_progress",
                    owner="team-lead")
    for i in range(5):
        _stage_task(
            tmp_path, team, f"sig-{i}",
            status="in_progress", owner=f"teammate-{i}",
            metadata={"completion_type": "signal", "type": "blocker"},
        )

    start = time.perf_counter()
    count = count_active_tasks(team)
    elapsed = time.perf_counter() - start

    # Only the 50 teammate-owned non-signal tasks count.
    assert count == 50, (
        f"Expected 50 active teammate tasks; got {count}. The other 50 "
        "are 30 umbrellas + 10 orphans + 5 lead-owned + 5 signals — "
        "all carved out."
    )
    assert elapsed < 5.0, (
        f"count_active_tasks took {elapsed:.3f}s on 100 tasks × 20 "
        "members. A linear-time implementation should finish in well "
        "under 1 s; this generous bound only catches quadratic-or-"
        "worse regressions."
    )


# ---------- Direct _classify_owner dataclass-shape coverage ----------
#
# `_classify_owner` is the single-read projection that consolidates the
# wake-side owner-classification logic into one `_iter_members` +
# `_read_team_lead_agent_id` pair. The helper wrappers
# (`_owner_is_known_team_member`, `_is_lead_owned`) and the step-4 inline
# check in `_lifecycle_relevant` are all consumers. The tests in this
# section pin the dataclass shape directly so a future refactor that
# changes a single field (e.g., flipping `config_readable` default,
# adding a new field, dropping `agent_type`) trips a counter-signal even
# if the wrappers and call-site continue to behave correctly via lucky
# branch coincidence.


def test_classify_owner_empty_team_name_returns_all_false(tmp_path, monkeypatch):
    """Empty team_name (default-arg path) bypasses config read entirely
    and returns the all-default classification. The call site treats
    this as fail-CONSERVATIVE fall-through identical to the empty-
    members case."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = wl._classify_owner("some-owner", "")
    assert isinstance(result, wl._OwnerClassification)
    assert result.is_known_team_member is False
    assert result.is_lead is False
    assert result.agent_type is None
    assert result.config_readable is False


def test_classify_owner_non_string_team_name_returns_all_false(tmp_path, monkeypatch):
    """Non-string team_name (e.g., None, int, list) is rejected at the
    same gate as empty team_name. The call site never reaches the
    `_iter_members` read."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for bad in (None, 42, [], {}, True):
        result = wl._classify_owner("some-owner", bad)
        assert isinstance(result, wl._OwnerClassification)
        assert result == wl._OwnerClassification(False, False, None, False), (
            f"team_name={bad!r} must produce the all-default classification."
        )


def test_classify_owner_empty_members_marks_config_unreadable(tmp_path, monkeypatch):
    """A non-empty team_name with no config on disk (so `_iter_members`
    returns []) signals `config_readable=False` so the call site can
    fail-CONSERVATIVE. The other fields stay default; the owner shape
    is not inspected once members come back empty."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # No team config written under tmp_path; _iter_members returns [].
    result = wl._classify_owner("some-owner", "team-no-config")
    assert result == wl._OwnerClassification(False, False, None, False)


def test_classify_owner_readable_config_with_bad_owner_marks_orphan(
    tmp_path, monkeypatch
):
    """Config readable, but the owner is non-string or empty. The
    classification distinguishes this from the empty-members case via
    `config_readable=True` so the call site treats it as an intentional
    exclusion (orphan / unowned), NOT a fail-CONSERVATIVE fall-through."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-bad-owner"
    _write_team_config(
        tmp_path, team,
        [
            {"name": "team-lead", "agentId": "team-lead@T", "agentType": "pact-orchestrator"},
            {"name": "architect", "agentId": "architect@T", "agentType": "pact-architect"},
        ],
        lead_agent_id="team-lead@T",
    )
    for bad_owner in (None, "", 42, [], {}, True):
        result = wl._classify_owner(bad_owner, team)
        assert result == wl._OwnerClassification(False, False, None, True), (
            f"owner={bad_owner!r} under readable config must be classified "
            "as config_readable=True / is_known_team_member=False (orphan)."
        )


def test_classify_owner_readable_config_no_name_match_marks_orphan(
    tmp_path, monkeypatch
):
    """Config readable, owner is a non-empty string, but no member's
    name matches. Same `(False, False, None, True)` shape as the
    bad-owner branch; the call site excludes via the
    `is_known_team_member=False` flag."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-no-match"
    _write_team_config(
        tmp_path, team,
        [
            {"name": "team-lead", "agentId": "team-lead@T", "agentType": "pact-orchestrator"},
            {"name": "architect", "agentId": "architect@T", "agentType": "pact-architect"},
        ],
        lead_agent_id="team-lead@T",
    )
    result = wl._classify_owner("ghost-owner", team)
    assert result == wl._OwnerClassification(False, False, None, True)


def test_classify_owner_teammate_match_marks_known_not_lead(tmp_path, monkeypatch):
    """Owner matches a non-lead team member. Classification reports
    `is_known_team_member=True`, `is_lead=False`, `agent_type` populated
    from the member's recorded agentType. This is the canonical
    teammate-task shape."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-teammate-match"
    _write_team_config(
        tmp_path, team,
        [
            {"name": "team-lead", "agentId": "team-lead@T", "agentType": "pact-orchestrator"},
            {"name": "architect", "agentId": "architect@T", "agentType": "pact-architect"},
        ],
        lead_agent_id="team-lead@T",
    )
    result = wl._classify_owner("architect", team)
    assert result == wl._OwnerClassification(True, False, "pact-architect", True)


def test_classify_owner_lead_match_marks_lead(tmp_path, monkeypatch):
    """Owner matches the team-lead member (whose agentId equals the
    team's leadAgentId). Classification reports `is_known_team_member=
    True`, `is_lead=True`, `agent_type` populated. The call site uses
    `is_lead` to short-circuit step-4 exclusion."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-lead-match"
    _write_team_config(
        tmp_path, team,
        [
            {"name": "team-lead", "agentId": "team-lead@T", "agentType": "pact-orchestrator"},
            {"name": "architect", "agentId": "architect@T", "agentType": "pact-architect"},
        ],
        lead_agent_id="team-lead@T",
    )
    result = wl._classify_owner("team-lead", team)
    assert result == wl._OwnerClassification(True, True, "pact-orchestrator", True)


def test_classify_owner_member_without_agenttype_yields_none_agent_type(
    tmp_path, monkeypatch
):
    """A team member entry without an `agentType` field (or with a
    non-string agentType) yields `agent_type=None` in the
    classification. The wake-excluded-agentType carve-out at step 3
    treats `None` as "no carve-out" and falls through to step 4."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-no-agenttype"
    _write_team_config(
        tmp_path, team,
        [
            {"name": "team-lead", "agentId": "team-lead@T"},  # no agentType
            {"name": "architect", "agentId": "architect@T", "agentType": 42},  # bad type
        ],
        lead_agent_id="team-lead@T",
    )
    # Lead match: agent_type stays None (field absent).
    assert wl._classify_owner("team-lead", team) == wl._OwnerClassification(
        True, True, None, True
    )
    # Teammate match: agent_type stays None (non-string value rejected).
    assert wl._classify_owner("architect", team) == wl._OwnerClassification(
        True, False, None, True
    )


# ---------- _warn_empty_team_config_once dedupe behavior ----------
#
# `_warn_empty_team_config_once` dedupes per-team within the same
# process via a module-level set (`_EMPTY_CONFIG_WARN_TEAMS`). The
# tests in this section pin: (a) the first call for a team produces a
# side effect (stderr in test contexts because session_journal is
# uninitialized), (b) the second call for the SAME team is a no-op
# (already in the set), (c) a DIFFERENT team triggers a fresh warning.
# Fixture clears `_EMPTY_CONFIG_WARN_TEAMS` between tests so each test
# starts from a known-empty state.


@pytest.fixture
def reset_empty_config_warn_set():
    """Module-level state isolation for `_EMPTY_CONFIG_WARN_TEAMS`.

    Snapshot the current set, clear it for the test body, restore the
    snapshot on teardown. Avoids cross-test contamination if a prior
    test happened to warm an entry.
    """
    snapshot = set(wl._EMPTY_CONFIG_WARN_TEAMS)
    wl._EMPTY_CONFIG_WARN_TEAMS.clear()
    yield
    wl._EMPTY_CONFIG_WARN_TEAMS.clear()
    wl._EMPTY_CONFIG_WARN_TEAMS.update(snapshot)


def test_warn_empty_team_config_once_first_call_writes(
    capsys, reset_empty_config_warn_set
):
    """First call for a team_name produces a [WAKE-TALLY WARN] line on
    stderr (the journal-fallback path; session_journal is uninitialized
    in the test process so the journal-first branch returns False and
    stderr fires). The team is added to the dedupe set."""
    assert "team-warn-A" not in wl._EMPTY_CONFIG_WARN_TEAMS
    wl._warn_empty_team_config_once("team-warn-A")
    captured = capsys.readouterr()
    assert "[WAKE-TALLY WARN]" in captured.err
    assert "team-warn-A" in captured.err
    assert "team-warn-A" in wl._EMPTY_CONFIG_WARN_TEAMS


def test_warn_empty_team_config_once_repeat_call_is_noop(
    capsys, reset_empty_config_warn_set
):
    """Second call for the SAME team_name is a no-op — already in the
    dedupe set, so the function returns immediately. No stderr line,
    no journal write attempt."""
    wl._warn_empty_team_config_once("team-warn-dup")
    _ = capsys.readouterr()  # drain the first-call output
    wl._warn_empty_team_config_once("team-warn-dup")
    captured = capsys.readouterr()
    assert captured.err == "", (
        "Repeat call for the same team must produce no stderr output. "
        f"Got: {captured.err!r}"
    )


def test_warn_empty_team_config_once_per_team_isolation(
    capsys, reset_empty_config_warn_set
):
    """Different team_names are tracked independently. team-A warning
    does not suppress a subsequent team-B warning; the dedupe set
    grows monotonically across distinct teams within the same
    process."""
    wl._warn_empty_team_config_once("team-iso-A")
    first = capsys.readouterr()
    assert "team-iso-A" in first.err

    wl._warn_empty_team_config_once("team-iso-B")
    second = capsys.readouterr()
    assert "[WAKE-TALLY WARN]" in second.err
    assert "team-iso-B" in second.err

    assert {"team-iso-A", "team-iso-B"} <= wl._EMPTY_CONFIG_WARN_TEAMS


def test_warn_empty_team_config_once_rejects_bad_team_name(
    capsys, reset_empty_config_warn_set
):
    """Non-string or empty team_name returns immediately without
    side effect. The dedupe set is not touched; no stderr line."""
    for bad in (None, "", 42, []):
        wl._warn_empty_team_config_once(bad)
    captured = capsys.readouterr()
    assert captured.err == "", (
        f"Bad team_name inputs must produce no stderr output. "
        f"Got: {captured.err!r}"
    )
    assert wl._EMPTY_CONFIG_WARN_TEAMS == set()
