"""
V8a unit tests for shared.wake_lifecycle.has_in_progress_umbrella_orchestration.

Predicate-isolation coverage of the umbrella-detection helper consumed
by both teardown emission sites (Tier-1 teardown_request_emitter Gate 6
+ Tier-2 wake_lifecycle_emitter._maybe_write_teammate_teardown_marker
Clause 4). Tests write minimal on-disk task files under tmp_path and
monkeypatch Path.home, matching the fixture pattern at
test_has_same_teammate_continuation.py.

Counter-test-by-revert (manual / runbook-documented): cp-bak the file,
delete the helper body (replace with `return False`), run this module —
expect cardinality {N tests, several fail covering positive cases;
negative-case tests still pass}. The negative-case tests are the
load-bearing structural pin that "False on no umbrella present" is the
helper's correct default.
"""

import json
from pathlib import Path

import pytest

import shared.wake_lifecycle as wl
from fixtures.disk_shapes import (
    UMBRELLA_SUBJECT_PREFIXES,
    make_specialist_task,
    make_team_config,
    make_umbrella_task,
)


def _write_task(home: Path, team: str, task: dict) -> None:
    """Write a task dict under ~/.claude/tasks/{team}/{id}.json."""
    tasks_dir = home / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / f"{task['id']}.json").write_text(
        json.dumps(task), encoding="utf-8"
    )


def _write_team_config(home: Path, team: str, config: dict) -> None:
    team_dir = home / ".claude" / "teams" / team
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )


class TestHasInProgressUmbrellaOrchestration:
    """V8a 7-cell unit matrix for the umbrella-detection predicate."""

    def test_umbrella_present_returns_true(self, tmp_path, monkeypatch):
        """Cell 1: an in_progress umbrella task with a canonical prefix
        is present → True (suppress teardown)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell1"
        _write_task(tmp_path, team, make_umbrella_task("U1"))
        assert wl.has_in_progress_umbrella_orchestration(team) is True

    def test_umbrella_absent_returns_false(self, tmp_path, monkeypatch):
        """Cell 2: no tasks present at all → False (emit teardown).
        Empty tasks dir is the baseline case the wake mechanism's
        over-arm posture handles cleanly."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell2"
        # Create the tasks dir but write nothing — emulates a clean team
        # with no in-flight work.
        (tmp_path / ".claude" / "tasks" / team).mkdir(parents=True)
        assert wl.has_in_progress_umbrella_orchestration(team) is False

    def test_umbrella_non_in_progress_returns_false(self, tmp_path, monkeypatch):
        """Cell 3: an umbrella exists but its status is `completed` (or
        `pending` — both are not in_progress) → False. The predicate
        only fires on actively-running orchestrations; a completed
        umbrella is exactly when teardown SHOULD emit."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell3"
        _write_task(
            tmp_path, team, make_umbrella_task("U1", status="completed")
        )
        _write_task(
            tmp_path, team, make_umbrella_task("U2", status="pending")
        )
        assert wl.has_in_progress_umbrella_orchestration(team) is False

    def test_non_umbrella_subject_returns_false(self, tmp_path, monkeypatch):
        """Cell 4: an in_progress task exists but its subject does not
        match any UMBRELLA_SUBJECT_PREFIXES entry → False. Specialist
        teammate tasks should not trip the umbrella-detection gate.
        This is the structural pin that signature-based detection
        does not over-match on arbitrary in_progress work."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell4"
        _write_task(
            tmp_path, team,
            make_specialist_task("S1", "backend-coder", subject="implement helper"),
        )
        assert wl.has_in_progress_umbrella_orchestration(team) is False

    def test_multi_umbrella_present_returns_true(self, tmp_path, monkeypatch):
        """Cell 5: multiple in_progress umbrellas with DIFFERENT canonical
        prefixes are present → True. The predicate uses ANY-match
        semantics; the first hit short-circuits. Mixing prefixes
        confirms no prefix is privileged."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell5"
        _write_task(
            tmp_path, team, make_umbrella_task("U1", subject_prefix="Feature: ")
        )
        _write_task(
            tmp_path, team, make_umbrella_task("U2", subject_prefix="ARCHITECT: ")
        )
        _write_task(
            tmp_path, team,
            make_specialist_task("S1", "backend-coder", subject="other work"),
        )
        assert wl.has_in_progress_umbrella_orchestration(team) is True

    def test_empty_team_name_returns_false(self, tmp_path, monkeypatch):
        """Cell 6: empty team_name → False (fail-CONSERVATIVE). The
        underlying iter_team_task_jsons rejects unsafe path components
        via is_safe_path_component; the empty string is rejected, so
        the iteration yields nothing and the predicate returns False.
        Pure-never-raises contract: no exception escapes."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert wl.has_in_progress_umbrella_orchestration("") is False

    def test_malformed_task_file_returns_false(self, tmp_path, monkeypatch):
        """Cell 7: a malformed task file under the team dir is silently
        skipped by iter_team_task_jsons; if no other umbrella is
        present, the predicate returns False. Pins the
        fail-CONSERVATIVE posture against parse-failed JSON — the wake
        mechanism over-arm-is-recoverable axiom is preserved
        (returning False here means teardown emits, which is the
        recoverable side)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-cell7"
        tasks_dir = tmp_path / ".claude" / "tasks" / team
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "bogus.json").write_text("not valid json{", encoding="utf-8")
        assert wl.has_in_progress_umbrella_orchestration(team) is False


class TestUmbrellaPrefixesContract:
    """Pin the UMBRELLA_SUBJECT_PREFIXES re-export contract: the test-
    side import is identity-equal to the production constant. If a
    future refactor accidentally redefines the tuple in disk_shapes.py
    instead of re-exporting, this test catches the drift before any
    fixture or production read goes inconsistent."""

    def test_test_side_tuple_is_identity_equal_to_production(self):
        """Re-export check via Python `is` operator. Identity (not just
        equality) confirms the tuple object originates at the production
        module — drift-resistance by import semantics."""
        assert UMBRELLA_SUBJECT_PREFIXES is wl.UMBRELLA_SUBJECT_PREFIXES

    def test_tuple_contents_are_locked_per_plan(self):
        """Pin the exact 7-element shape per plan L133-141. Adding /
        removing a prefix is a contract change that requires updating
        both the production constant AND this test in the same PR."""
        assert wl.UMBRELLA_SUBJECT_PREFIXES == (
            "Feature: ",
            "Plan: ",
            "Plan (revised): ",
            "PREPARE: ",
            "ARCHITECT: ",
            "CODE: ",
            "TEST: ",
        )


class TestSignalTaskCarveOut:
    """Pin the signal-task carve-out: a hypothetical in_progress
    umbrella with `metadata.completion_type == "signal"` is excluded
    from the predicate. Structurally impossible today (umbrellas don't
    carry the signal metadata) but defended against because
    count_active_tasks's tally applies the same carve-out and Gate 6
    must stay consistent with the count it short-circuits."""

    def test_signal_typed_umbrella_excluded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "team-signal"
        signal_umbrella = make_umbrella_task("U1")
        signal_umbrella["metadata"] = {"completion_type": "signal", "type": "blocker"}
        _write_task(tmp_path, team, signal_umbrella)
        assert wl.has_in_progress_umbrella_orchestration(team) is False
