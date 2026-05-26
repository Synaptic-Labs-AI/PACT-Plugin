"""
Per-dispatch variety stamping — traversal-helper coverage.

Covers _resolve_required_band_via_blocks in task_lifecycle_gate.py: the
disk-based traversal from Task A (teachback subject) through blocks[0] to
Task B's metadata.variety.total. This is the helper that R3 consumes to
decide whether reasoning_reconstruction is REQUIRED.

Test surface architecture (per the design doc §4.2):
  - All 4 return values exercised: "required", "recommended", "skipped",
    "unresolvable".
  - All fail-open paths: blocks missing, blocks empty, Task B id
    non-string, Task B file missing, Task B missing metadata, Task B
    missing variety, variety.total non-int.
  - Defensive: Task A is {} (read_task_json returned empty) → unresolvable.
  - Defensive: team_name is "" → unresolvable.

Schema-validator pure-function tests (the D10 + D11 validators) live in
test_task_lifecycle_gate.py alongside the integration tests, mirroring
the existing _validate_handoff_schema co-location pattern. This file is
the traversal-helper surface only.

Divergence-computation helper + its tests are intentionally deferred to
the wrap-up retrospective integration commit per lead resolution (the
helper's canonical home is shared/variety_divergence.py, consumed by
wrap-up.md; introducing it here in this commit and then refactoring would
create inter-commit churn).
"""

import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import task_lifecycle_gate as tlg  # noqa: E402


# =============================================================================
# Helpers
# =============================================================================


def _well_formed_variety(**overrides):
    """Return a well-formed metadata.variety dict per D11."""
    payload = {
        "novelty": 2,
        "novelty_rationale": "x",
        "scope": 2,
        "scope_rationale": "x",
        "uncertainty": 2,
        "uncertainty_rationale": "x",
        "risk": 2,
        "risk_rationale": "x",
        "total": 8,
    }
    payload.update(overrides)
    return payload


def _seed_task_b(
    tmp_path, monkeypatch, team_name, task_b_id,
    metadata=None,
):
    """Seed Task B at ~/.claude/tasks/{team_name}/{task_b_id}.json with
    the given metadata dict (or empty if None)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tasks_dir = tmp_path / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task_b = {
        "id": task_b_id,
        "subject": "implement foo",
        "owner": "pact-backend-coder",
        "metadata": metadata if metadata is not None else {},
    }
    (tasks_dir / f"{task_b_id}.json").write_text(
        json.dumps(task_b), encoding="utf-8"
    )


# =============================================================================
# Return-value coverage: required / recommended / skipped
# =============================================================================


class TestRequiredBandResolution:
    """The 3 successful-resolution return values: required, recommended,
    skipped. Each exercises the variety.total threshold logic."""

    def test_required_at_min_threshold(self, tmp_path, monkeypatch):
        """total = 11 → required (inclusive lower bound for REQUIRED band).
        Pinned via _REASONING_RECONSTRUCTION_REQUIRED_MIN."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=11)},
        )
        task_a = {"blocks": ["2"]}
        assert tlg._resolve_required_band_via_blocks(
            task_a, "test-team"
        ) == "required"

    def test_required_at_high_score(self, tmp_path, monkeypatch):
        """total = 16 (max possible) → required."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=16)},
        )
        task_a = {"blocks": ["2"]}
        assert tlg._resolve_required_band_via_blocks(
            task_a, "test-team"
        ) == "required"

    def test_recommended_at_lower_threshold(self, tmp_path, monkeypatch):
        """total = 7 → recommended (inclusive lower bound for the
        recommended band per variety_scorer.COMPACT_MAX = 6)."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=7)},
        )
        task_a = {"blocks": ["2"]}
        assert tlg._resolve_required_band_via_blocks(
            task_a, "test-team"
        ) == "recommended"

    def test_recommended_at_upper_threshold(self, tmp_path, monkeypatch):
        """total = 10 → recommended (inclusive upper bound per
        variety_scorer.ORCHESTRATE_MAX = 10)."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=10)},
        )
        task_a = {"blocks": ["2"]}
        assert tlg._resolve_required_band_via_blocks(
            task_a, "test-team"
        ) == "recommended"

    def test_skipped_at_upper_threshold(self, tmp_path, monkeypatch):
        """total = 6 → skipped (inclusive upper bound per
        variety_scorer.COMPACT_MAX = 6)."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=6)},
        )
        task_a = {"blocks": ["2"]}
        assert tlg._resolve_required_band_via_blocks(
            task_a, "test-team"
        ) == "skipped"

    def test_skipped_at_min_score(self, tmp_path, monkeypatch):
        """total = 4 (min possible per variety_scorer.MIN_SCORE) → skipped."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=4)},
        )
        task_a = {"blocks": ["2"]}
        assert tlg._resolve_required_band_via_blocks(
            task_a, "test-team"
        ) == "skipped"

    def test_band_threshold_constants_aligned_with_variety_scorer(self):
        """Pin the alignment between _REASONING_RECONSTRUCTION_REQUIRED_MIN
        and variety_scorer.ORCHESTRATE_MAX. PLAN_MODE_MIN-implied threshold
        is ORCHESTRATE_MAX + 1 = 11. If variety_scorer's thresholds shift
        and this module's constant doesn't, this test fails and the drift
        is surfaced. The design doc §6.5 codifies this as a grep-at-edit-
        time discipline until the SSOT migration trajectory lands."""
        from shared import variety_scorer

        assert (
            tlg._REASONING_RECONSTRUCTION_REQUIRED_MIN
            == variety_scorer.ORCHESTRATE_MAX + 1
        ), (
            "module-local constant drifted from variety_scorer SSOT — "
            "see task_lifecycle_gate.py inline comment + design doc §6.5"
        )


# =============================================================================
# Fail-open paths: every "unresolvable" return path
# =============================================================================


class TestBandUnresolvableFailOpen:
    """All paths returning "unresolvable". Fail-open by design: each
    failure mode keeps traversal silent so the caller can emit the
    band_unresolvable advisory documenting the gap."""

    def test_task_a_empty_dict(self):
        """read_task_json returns {} → no `blocks` key → unresolvable."""
        assert tlg._resolve_required_band_via_blocks(
            {}, "test-team"
        ) == "unresolvable"

    def test_blocks_key_absent(self):
        """Task A has no blocks key → unresolvable."""
        assert tlg._resolve_required_band_via_blocks(
            {"subject": "x"}, "test-team"
        ) == "unresolvable"

    def test_blocks_empty_list(self):
        """Task A.blocks = [] → unresolvable."""
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": []}, "test-team"
        ) == "unresolvable"

    def test_blocks_is_not_list(self):
        """Task A.blocks = "2" (string, not list) → unresolvable."""
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": "2"}, "test-team"
        ) == "unresolvable"

    def test_blocks_first_id_non_string(self):
        """Task A.blocks = [123] (int, not string) → unresolvable."""
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": [123]}, "test-team"
        ) == "unresolvable"

    def test_blocks_first_id_empty_string(self):
        """Task A.blocks = [""] → unresolvable."""
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": [""]}, "test-team"
        ) == "unresolvable"

    def test_empty_team_name(self):
        """team_name = "" → unresolvable (cannot resolve disk path)."""
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, ""
        ) == "unresolvable"

    def test_task_b_file_missing(self, tmp_path, monkeypatch):
        """Task A.blocks=['999'] but no 999.json on disk → unresolvable."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Create the team directory but no task file
        (tmp_path / ".claude" / "tasks" / "test-team").mkdir(parents=True)
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["999"]}, "test-team"
        ) == "unresolvable"

    def test_task_b_missing_metadata(self, tmp_path, monkeypatch):
        """Task B exists but no metadata key → unresolvable."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        tasks_dir = tmp_path / ".claude" / "tasks" / "test-team"
        tasks_dir.mkdir(parents=True)
        task_b = {"id": "2", "subject": "x", "owner": "x"}
        (tasks_dir / "2.json").write_text(json.dumps(task_b), encoding="utf-8")
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_task_b_metadata_not_dict(self, tmp_path, monkeypatch):
        """Task B.metadata is a string → unresolvable."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2", metadata="not a dict",
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_task_b_missing_variety(self, tmp_path, monkeypatch):
        """Task B.metadata is empty dict, no variety key → unresolvable."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2", metadata={},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_task_b_variety_not_dict(self, tmp_path, monkeypatch):
        """Task B.metadata.variety is a string → unresolvable."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": "not a dict"},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_variety_total_missing(self, tmp_path, monkeypatch):
        """Task B.metadata.variety has no `total` key → unresolvable."""
        variety = _well_formed_variety()
        variety.pop("total")
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": variety},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_variety_total_non_int_string(self, tmp_path, monkeypatch):
        """Task B.metadata.variety.total = "twelve" → unresolvable."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total="twelve")},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_variety_total_bool_rejected(self, tmp_path, monkeypatch):
        """Task B.metadata.variety.total = True → unresolvable.
        Defensive: bool is a subclass of int in Python; reject explicitly.
        """
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=True)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_variety_total_float(self, tmp_path, monkeypatch):
        """Task B.metadata.variety.total = 12.5 → unresolvable. The
        traversal accepts ints only; floats indicate malformed data."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=12.5)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"


# =============================================================================
# Multi-block defensiveness: first-block convention
# =============================================================================


class TestMultiBlockTraversal:
    """The traversal takes blocks[0] as the canonical Task B pointer.
    Multi-block teachback tasks are not in current convention; this pins
    the behavior so a future schema change explicitly opts in."""

    def test_first_block_is_canonical_work_task(
        self, tmp_path, monkeypatch,
    ):
        """blocks = ['2', '999'] — first id resolves; second is ignored.
        If '2' exists with REQUIRED-band variety, the helper returns
        required regardless of '999' being absent."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=12)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2", "999"]}, "test-team"
        ) == "required"

    def test_first_block_dominates_even_if_skipped(
        self, tmp_path, monkeypatch,
    ):
        """blocks = ['low', 'high'] — first resolves to skipped; second
        is ignored even if it would have been required. Pins the
        first-block convention against a future "max-over-blocks" drift."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "low",
            metadata={"variety": _well_formed_variety(total=4)},
        )
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "high",
            metadata={"variety": _well_formed_variety(total=15)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["low", "high"]}, "test-team"
        ) == "skipped"
