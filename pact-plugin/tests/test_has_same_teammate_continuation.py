"""
Predicate-isolation tests for shared.wake_lifecycle.has_same_teammate_continuation.

Architect-spec 8-cell + production-shape regression. The 8 cells exercise
the predicate's logical decision table; the 9th (production-shape) cell
asserts the predicate works against the on-disk `blocks` field which is
the field actually populated in a real PACT session (per empirical capture
of ~/.claude/tasks/<team>/*.json across 24 task files in session
pact-8159e827 — `addBlocks` is null on every persisted task; `blocks` is
the populated forward-pointing linkage).

Counter-test-by-revert (manual / runbook-documented): cp-bak the file,
`git checkout HEAD~1 -- pact-plugin/hooks/shared/wake_lifecycle.py`, run
this module — expect cardinality {8 fail, 1 collection error} (8 cells
fail because helper is gone; production-shape cell errors on import).
See pact-plugin/tests/runbooks/wake-lifecycle-teachback-rearm.md.
"""

import json
from pathlib import Path

import pytest

import shared.wake_lifecycle as wl


def _write_team_config(home: Path, team: str, members: list[dict]) -> None:
    team_dir = home / ".claude" / "teams" / team
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"team_name": team, "members": members}),
        encoding="utf-8",
    )


def _write_task_file(home: Path, team: str, task_id: str, **fields) -> None:
    """Write a task file under ~/.claude/tasks/<team>/<id>.json. The
    payload mirrors the on-disk shape that read_task_json returns."""
    tasks_dir = home / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


# ---------- Architect-spec 8-cell predicate matrix ----------


class TestHasSameTeammateContinuationCells:
    """8-cell predicate matrix from the architect HANDOFF design_artifacts_v3
    test_strategy_inputs_for_test_engineer_v3.

    Each cell exercises one row of the decision table. The cells use the
    `addBlocks` field on the completed task because that is the field
    backend-coder's helper reads at HEAD. A separate production-shape
    test class (below) covers the `blocks` field which is what the on-
    disk fixture actually populates.
    """

    def test_cell_1_pending_same_owner_defers(self, tmp_path, monkeypatch):
        """Cell (1): completed task has addBlocks=[X], X.owner==same
        teammate, X.status==pending → True (defer Teardown)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell1"
        _write_task_file(tmp_path, team, "B", status="pending", owner="backend-coder")
        completed = {
            "id": "A", "owner": "backend-coder", "status": "completed",
            "addBlocks": ["B"],
        }
        assert wl.has_same_teammate_continuation(completed, team) is True

    def test_cell_2_in_progress_same_owner_defers(self, tmp_path, monkeypatch):
        """Cell (2): X.status==in_progress + same owner → True (defer;
        teammate already claimed the continuation)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell2"
        _write_task_file(tmp_path, team, "B", status="in_progress", owner="backend-coder")
        completed = {
            "id": "A", "owner": "backend-coder", "status": "completed",
            "addBlocks": ["B"],
        }
        assert wl.has_same_teammate_continuation(completed, team) is True

    def test_cell_3_different_owner_emits(self, tmp_path, monkeypatch):
        """Cell (3): X.owner==different teammate → False (emit Teardown)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell3"
        _write_task_file(tmp_path, team, "B", status="pending", owner="test-engineer")
        completed = {
            "id": "A", "owner": "backend-coder", "status": "completed",
            "addBlocks": ["B"],
        }
        assert wl.has_same_teammate_continuation(completed, team) is False

    def test_cell_4_empty_addBlocks_emits(self, tmp_path, monkeypatch):
        """Cell (4): addBlocks empty → False."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell4"
        completed = {
            "id": "A", "owner": "backend-coder", "status": "completed",
            "addBlocks": [],
        }
        assert wl.has_same_teammate_continuation(completed, team) is False

    def test_cell_5_signal_task_not_lifecycle_relevant_emits(self, tmp_path, monkeypatch):
        """Cell (5): X is signal-task → False (signal-task is not
        lifecycle-relevant; same-owner match is not enough on its own)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell5"
        _write_task_file(
            tmp_path, team, "B",
            status="pending", owner="backend-coder",
            metadata={"completion_type": "signal", "type": "blocker"},
        )
        completed = {
            "id": "A", "owner": "backend-coder", "status": "completed",
            "addBlocks": ["B"],
        }
        assert wl.has_same_teammate_continuation(completed, team) is False

    def test_cell_6_exempt_agenttype_owner_not_lifecycle_relevant_emits(self, tmp_path, monkeypatch):
        """Cell (6): X.owner is exempt agentType (e.g., pact-secretary)
        → False (exempt agentType is not lifecycle-relevant). The same-
        owner-match condition is satisfied (both A and B owned by
        secretary) but _lifecycle_relevant excludes B from consideration
        via the team-config agentType carve-out."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell6"
        _write_team_config(tmp_path, team, [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        _write_task_file(tmp_path, team, "B", status="pending", owner="session-secretary")
        completed = {
            "id": "A", "owner": "session-secretary", "status": "completed",
            "addBlocks": ["B"],
        }
        assert wl.has_same_teammate_continuation(completed, team) is False

    def test_cell_7_completed_continuation_emits(self, tmp_path, monkeypatch):
        """Cell (7): X.status==completed → False (no in-flight
        continuation; the chain has terminated)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell7"
        _write_task_file(tmp_path, team, "B", status="completed", owner="backend-coder")
        completed = {
            "id": "A", "owner": "backend-coder", "status": "completed",
            "addBlocks": ["B"],
        }
        assert wl.has_same_teammate_continuation(completed, team) is False

    def test_cell_8_nonexistent_continuation_emits(self, tmp_path, monkeypatch):
        """Cell (8): X non-existent on disk (race-deleted) → False
        (conservative: emit Teardown, which is idempotent at the skill
        layer). Pin the fail-closed behavior — fail-open here would
        silently suppress legitimate Teardowns."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell8"
        # Deliberately do NOT write a task file for "B".
        completed = {
            "id": "A", "owner": "backend-coder", "status": "completed",
            "addBlocks": ["B"],
        }
        assert wl.has_same_teammate_continuation(completed, team) is False


# ---------- Pure-never-raises adversarial sweep ----------


class TestHasSameTeammateContinuationNeverRaises:
    """Pure-function contract: predicate must NEVER raise, regardless of
    input shape. Fail-closed (False) on every error path. Mirrors the
    sibling adversarial sweep on _lifecycle_relevant."""

    @pytest.mark.parametrize("bad_input", [
        None, [], 42, "string", True, b"bytes",
    ])
    def test_never_raises_on_non_dict_completed_task(self, bad_input, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        try:
            result = wl.has_same_teammate_continuation(bad_input, "team-x")
        except Exception as exc:
            pytest.fail(f"raised on completed_task={bad_input!r}: {exc}")
        assert result is False

    @pytest.mark.parametrize("bad_owner", [
        None, "", 42, [], {}, True,
    ])
    def test_never_raises_on_non_string_owner(self, bad_owner, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        completed = {"id": "A", "owner": bad_owner, "addBlocks": []}
        try:
            result = wl.has_same_teammate_continuation(completed, "team-x")
        except Exception as exc:
            pytest.fail(f"raised on owner={bad_owner!r}: {exc}")
        assert result is False

    @pytest.mark.parametrize("bad_addBlocks", [
        None, "string", 42, {}, True,
    ])
    def test_never_raises_on_non_list_addBlocks(self, bad_addBlocks, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        completed = {"id": "A", "owner": "x", "addBlocks": bad_addBlocks}
        try:
            result = wl.has_same_teammate_continuation(completed, "team-x")
        except Exception as exc:
            pytest.fail(f"raised on addBlocks={bad_addBlocks!r}: {exc}")
        assert result is False

    @pytest.mark.parametrize("bad_id_entry", [
        None, "", 42, [], {}, True,
    ])
    def test_never_raises_on_non_string_id_entry(self, bad_id_entry, tmp_path, monkeypatch):
        """Adversarial: addBlocks list contains a non-string entry —
        skip silently and continue. Pins per-entry skip + fail-closed
        (no match found → False)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        completed = {"id": "A", "owner": "x", "addBlocks": [bad_id_entry]}
        try:
            result = wl.has_same_teammate_continuation(completed, "team-x")
        except Exception as exc:
            pytest.fail(f"raised on id-entry={bad_id_entry!r}: {exc}")
        assert result is False


# ---------- Production-shape regression: blocks vs addBlocks ----------


class TestHasSameTeammateContinuationProductionShape:
    """Production-shape regression test.

    Empirical observation from session pact-8159e827 (2026-05-09):
    100% of persisted task files in ~/.claude/tasks/<team>/*.json have
    `addBlocks: null` and `blocks: [...]` populated. The architect
    HANDOFF design specs the predicate to read `addBlocks` but on-disk
    that field is null — the predicate as written returns False on every
    real two-task dispatch and the Bug A defer-Teardown branch is
    INERT against actual production traffic.

    This test asserts the predicate works against the on-disk shape
    (blocks populated, addBlocks null). If backend-coder's predicate
    only reads `addBlocks`, this test FAILS RED — surfacing the gap.
    Once backend-coder reads `blocks` (or `addBlocks or blocks`), this
    test passes.

    REGRESSION DEFENSE: a future LLM editing the predicate to "simplify"
    by dropping the blocks fallback would re-introduce the inertness;
    this test catches it.
    """

    def test_predicate_handles_production_blocks_shape(self, tmp_path, monkeypatch):
        """Production-shape: completed task has `blocks: ['B']` and
        `addBlocks: null` (the empirical shape on disk in every PACT
        session captured to date). Same-owner same-teammate continuation
        is staged. Predicate must return True. If it returns False, the
        Bug A defer-Teardown branch is inert against real traffic."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-prod"
        _write_task_file(tmp_path, team, "B", status="pending", owner="backend-coder")
        completed = {
            "id": "A", "owner": "backend-coder", "status": "completed",
            "addBlocks": None,        # The empirical on-disk shape.
            "blocks": ["B"],          # The actually-populated linkage.
        }
        assert wl.has_same_teammate_continuation(completed, team) is True, (
            "Predicate failed to detect same-teammate continuation when "
            "the linkage is recorded in `blocks` (the actually-populated "
            "field on disk) rather than `addBlocks` (which is null on "
            "every production task — empirical from session pact-8159e827, "
            "24/24 task files). The predicate as written is INERT against "
            "real production traffic; Bug A defer-Teardown branch never "
            "fires. Fix: read `completed_task.get('addBlocks') or "
            "completed_task.get('blocks') or []` (or just read `blocks` "
            "since that is the on-disk field)."
        )

    def test_predicate_handles_both_addBlocks_and_blocks(self, tmp_path, monkeypatch):
        """Both fields populated (forward-compat with a future input-side
        TaskUpdate API that surfaces `addBlocks` as a non-null field).
        Either field's same-teammate match should defer."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-both"
        _write_task_file(tmp_path, team, "B", status="pending", owner="backend-coder")
        completed = {
            "id": "A", "owner": "backend-coder", "status": "completed",
            "addBlocks": ["B"],
            "blocks": ["B"],
        }
        assert wl.has_same_teammate_continuation(completed, team) is True

    def test_predicate_emits_when_both_fields_empty(self, tmp_path, monkeypatch):
        """Both fields explicitly empty (no continuation chain) — predicate
        returns False. Negative pair for the production-shape positive
        test above."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-empty"
        completed = {
            "id": "A", "owner": "backend-coder", "status": "completed",
            "addBlocks": None,
            "blocks": [],
        }
        assert wl.has_same_teammate_continuation(completed, team) is False
