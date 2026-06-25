"""
Per-dispatch variety stamping — traversal-helper coverage.

Covers _resolve_required_band_via_blocks in task_lifecycle_gate.py: the
disk-based traversal from Task A (teachback subject) through blocks[0] to
Task B's metadata.variety.total. This is the helper that R3 consumes to
decide whether reasoning_reconstruction is REQUIRED.

Test surface architecture:
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

Divergence-computation helper + its tests live in the second half of
this file (class TestVarietyDivergence). The helper's canonical home is
shared/variety_divergence.py, consumed by wrap-up.md §4 Orchestration
Retrospective composer. Tests cover the architect3 §4.3 catalog: positive
surfacing (overshoot / undershoot), negative (within-threshold), and edge
cases (None feature variety, empty dispatches, mixed coverage per D8).
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


def _no_fallback_variety(**overrides):
    """Return a variety dict whose `total` is the ONLY total candidate —
    the four dimension scores are omitted so the dimension-sum fallback
    cannot resolve, and there is no `score` key. Use this to exercise the
    "every resolver candidate invalid → unresolvable" path: override `total`
    with a bad value (or pop it) and no other candidate can recover it. The
    rationales (not resolution candidates) are kept so the write-time schema
    check still treats the shape as rationale-complete."""
    payload = {
        "novelty_rationale": "x",
        "scope_rationale": "x",
        "uncertainty_rationale": "x",
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
        Pinned via TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN."""
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
        """Pin the alignment between
        TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN and
        variety_scorer. The threshold binds to PLAN_MODE_MIN, which is itself
        ORCHESTRATE_MAX + 1 = 11. If variety_scorer's thresholds shift and
        this module's constant doesn't, this test fails and the drift is
        surfaced."""
        from shared import variety_scorer
        from shared.teachback_schema import (
            TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
        )

        assert (
            TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN
            == variety_scorer.ORCHESTRATE_MAX + 1
        ), (
            "shared.teachback_schema constant drifted from variety_scorer "
            "SSOT — see teachback_schema.py inline comment"
        )


# =============================================================================
# Resolvable-via-fallback: non-canonical stamps that now resolve a band
#
# Before the shared-resolver fix, only a strict int `total` resolved; a stamp
# carrying a non-canonical score / top-level variety_score / valid dimension
# scores read as "unresolvable". These cases pin that such stamps now resolve
# to the correct band — the false-positive the fix removes. They exercise the
# caller's int→band mapping through each fallback candidate, so a regression
# in either the resolver chain or the band cuts surfaces here.
# =============================================================================


class TestBandResolvableViaFallback:
    """Non-canonical variety stamps resolve to a band via the shared
    resolver's fallback chain, instead of reading as unresolvable."""

    def test_score_fallback_resolves_band_for_field_report_shape(
        self, tmp_path, monkeypatch,
    ):
        """The exact reported false-positive shape: rationales + a
        non-canonical `score` int (no `total`), with a sibling top-level
        `variety_score`. Must resolve to a band — NOT unresolvable."""
        variety = _no_fallback_variety(score=12)
        variety.pop("total")
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": variety, "variety_score": 12},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "required"

    def test_score_fallback_maps_to_recommended_band(
        self, tmp_path, monkeypatch,
    ):
        """score=8 (no total) → recommended band (7..10)."""
        variety = _no_fallback_variety(score=8)
        variety.pop("total")
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": variety},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "recommended"

    def test_top_level_variety_score_fallback_resolves_band(
        self, tmp_path, monkeypatch,
    ):
        """No `total`, no `score`, but a top-level metadata.variety_score
        int → resolves via candidate 3 (reachable because the caller passes
        Task B's full metadata to the resolver)."""
        variety = _no_fallback_variety()
        variety.pop("total")
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": variety, "variety_score": 11},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "required"

    def test_dimension_sum_fallback_resolves_band(
        self, tmp_path, monkeypatch,
    ):
        """No total/score/variety_score, but all four dimension scores
        valid → resolves to the sum's band. _well_formed_variety carries
        2/2/2/2 = 8, so popping `total` lands on recommended via the sum."""
        variety = _well_formed_variety()
        variety.pop("total")
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": variety},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "recommended"

    def test_junk_total_falls_through_to_dimension_sum_band(
        self, tmp_path, monkeypatch,
    ):
        """An out-of-range `total` does not shadow a recoverable dimension
        sum: total=99 with dims 3/3/3/3=12 → required (the sum's band)."""
        variety = _well_formed_variety(
            total=99, novelty=3, scope=3, uncertainty=3, risk=3,
        )
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": variety},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "required"


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

    def test_variety_total_missing_and_no_fallback(self, tmp_path, monkeypatch):
        """Task B.metadata.variety has no `total` key AND no recoverable
        fallback (dimensions stripped) → unresolvable. A missing total alone
        now resolves via the dimension-sum fallback when the four dimensions
        are valid; unresolvable requires every candidate to be invalid."""
        variety = _no_fallback_variety()
        variety.pop("total")
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": variety},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_variety_total_non_int_string_and_no_fallback(self, tmp_path, monkeypatch):
        """Task B.metadata.variety.total = "twelve" with no recoverable
        fallback → unresolvable."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _no_fallback_variety(total="twelve")},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_variety_total_bool_rejected_and_no_fallback(self, tmp_path, monkeypatch):
        """Task B.metadata.variety.total = True with no recoverable fallback
        → unresolvable. Defensive: bool is a subclass of int in Python;
        reject explicitly."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _no_fallback_variety(total=True)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_variety_total_float_and_no_fallback(self, tmp_path, monkeypatch):
        """Task B.metadata.variety.total = 12.5 with no recoverable fallback
        → unresolvable. The resolver accepts ints only; floats indicate
        malformed data."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _no_fallback_variety(total=12.5)},
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


# =============================================================================
# #891 Opt2: parent-inheritance fallback (_inherit_band_from_parent)
# =============================================================================
#
# When Task B carries no resolvable variety, the band is inherited from the
# PARENT (Plan/feature/umbrella) task that Task B blocks. These tests pin both
# halves of the C2 contract:
#   - RESOLVE: an unstamped Task B whose singleton blocks[0] points at a
#     STAMPED parent inherits the parent's REAL band (the #891 fix — a
#     consultation Task B that would mis-resolve as "skipped" now resolves to
#     the parent's 11-13 reality).
#   - FAIL-OPEN GUARDRAIL: never inherit a WRONG parent's band. Ambiguous
#     blocks (>1 entry, empty, non-list) or a parent that is not itself
#     stamped → "unresolvable" (the preserved floor), NOT a guess.
# =============================================================================


def _seed_task(
    tmp_path, monkeypatch, team_name, task_id, *, metadata=None, blocks=None,
):
    """Seed an arbitrary task on disk with optional `blocks` + `metadata`.
    Used to build the Task B → parent chain the inheritance fallback walks."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tasks_dir = tmp_path / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task = {"id": task_id, "subject": "x", "owner": "pact-backend-coder"}
    if blocks is not None:
        task["blocks"] = blocks
    if metadata is not None:
        task["metadata"] = metadata
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps(task), encoding="utf-8"
    )


class TestParentInheritanceFallback:
    """#891 Opt2: an unstamped Task B inherits its parent's band."""

    # ----- RESOLVE: parent inheritance maps to the parent's real band ----

    def test_inherits_required_band_from_parent(self, tmp_path, monkeypatch):
        """Task B has no variety but blocks a stamped parent (total=12) →
        the band resolves to the parent's REQUIRED band instead of
        unresolvable. The #891 consultation-Task-B fix."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["100"],
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100",
            metadata={"variety": _well_formed_variety(total=12)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "required"

    def test_inherits_recommended_band_from_parent(self, tmp_path, monkeypatch):
        """Parent total=9 → inherited band is recommended."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["100"],
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100",
            metadata={"variety": _well_formed_variety(total=9)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "recommended"

    def test_inherits_skipped_band_from_parent(self, tmp_path, monkeypatch):
        """Parent total=5 → inherited band is skipped (the parent's REAL
        low band; inheritance is not biased toward required)."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["100"],
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100",
            metadata={"variety": _well_formed_variety(total=5)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "skipped"

    def test_stamped_task_b_does_not_inherit(self, tmp_path, monkeypatch):
        """When Task B IS stamped, its OWN band wins — the parent is never
        consulted (inheritance is a fallback, not an override). Task B
        total=7 (recommended); parent total=16 (required) is ignored."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=7)},
            blocks=["100"],
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100",
            metadata={"variety": _well_formed_variety(total=16)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "recommended"

    # ----- FAIL-OPEN GUARDRAILS: never inherit a WRONG parent ------------

    def test_unstamped_b_no_blocks_is_unresolvable(self, tmp_path, monkeypatch):
        """Unstamped Task B with NO blocks pointer → no parent to inherit
        from → unresolvable (floor preserved). This is the legacy shape;
        pins that inheritance does not regress it."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2", metadata={},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_unstamped_b_multi_block_fails_open(self, tmp_path, monkeypatch):
        """Task B.blocks has >1 entry → AMBIGUOUS parent → fail-open to
        unresolvable rather than guess blocks[0]. The lead's
        don't-blindly-trust-blocks[0] guardrail: a wrong-parent inherit
        mis-resolves the band, the exact bug being fixed."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["100", "200"],
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100",
            metadata={"variety": _well_formed_variety(total=12)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_unstamped_b_unstamped_parent_fails_open(self, tmp_path, monkeypatch):
        """Singleton blocks[0] points at a parent that is NOT itself stamped
        → fail-open to unresolvable. The structural 'looks like a
        Plan/feature task' guardrail: only a stamped task is an inheritable
        parent, so a mis-pointed blocks[0] (e.g. at a phase/teachback task)
        cannot inherit a wrong band."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["100"],
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100", metadata={},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_unstamped_b_missing_parent_file_fails_open(self, tmp_path, monkeypatch):
        """blocks[0] points at a parent id with no file on disk → fail-open
        to unresolvable (read_task_json returns {})."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["999"],
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"


# =============================================================================
# Divergence-computation helper (shared/variety_divergence.py)
# =============================================================================
#
# Pure-function math consumed by wrap-up.md §4 Orchestration Retrospective
# composer. Tests pin the architect3 §4.3 catalog: positive surfacing
# (overshoot / undershoot), negative (within-threshold), and edge cases
# (None feature variety, empty dispatches, mixed coverage per D8).
# =============================================================================


from shared.variety_divergence import (  # noqa: E402
    DEFAULT_THRESHOLD,
    compute_variety_divergence,
)


class TestVarietyDivergence:
    """Architect3 §4.3 catalog for compute_variety_divergence."""

    # ----- Positive surfacing (architect3 §4.3 cases 1-2) ---------------

    def test_overshoot_surfaced(self):
        """Feature 9, dispatches [5,5,5,5,5] → delta 4 surfaced overshoot."""
        result = compute_variety_divergence(9, [5, 5, 5, 5, 5])
        assert result["surfaced"] is True
        assert result["direction"] == "overshot"
        assert result["delta"] == 4
        assert result["mean"] == 5
        assert result["max"] == 5
        assert result["min"] == 5
        assert result["coverage"] == 1.0
        assert result["reason"] is None

    def test_undershoot_surfaced(self):
        """Feature 5, dispatches [8,8,8] → delta 3 surfaced undershoot."""
        result = compute_variety_divergence(5, [8, 8, 8])
        assert result["surfaced"] is True
        assert result["direction"] == "undershot"
        assert result["delta"] == 3
        assert result["mean"] == 8

    # ----- Negative (within-threshold) (architect3 §4.3 cases 3-4) ------

    def test_within_threshold_zero_delta(self):
        """Feature 8, dispatches [7,8,9] → delta 0, surfaced=False."""
        result = compute_variety_divergence(8, [7, 8, 9])
        assert result["surfaced"] is False
        assert result["direction"] is None
        assert result["delta"] == 0
        assert result["mean"] == 8
        assert result["reason"] == "within_threshold"

    def test_within_threshold_symmetric_spread(self):
        """Feature 8, dispatches [6,8,10] → mean 8 delta 0, surfaced=False.
        The max/min spread is wide but mean lands on feature_variety."""
        result = compute_variety_divergence(8, [6, 8, 10])
        assert result["surfaced"] is False
        assert result["delta"] == 0
        assert result["max"] == 10
        assert result["min"] == 6

    def test_within_threshold_delta_one(self):
        """Boundary: delta=1 (below default threshold=2) is NOT surfaced.
        Pins the threshold semantic against an inclusive-vs-exclusive
        drift (`>=` not `>`)."""
        result = compute_variety_divergence(9, [8, 8, 8])
        assert result["surfaced"] is False
        assert result["delta"] == 1
        assert result["reason"] == "within_threshold"

    def test_threshold_boundary_delta_two_surfaced(self):
        """Boundary: delta=2 IS surfaced (>= threshold). Counter-pin to
        the delta=1 case above."""
        result = compute_variety_divergence(10, [8, 8, 8])
        assert result["surfaced"] is True
        assert result["delta"] == 2
        assert result["direction"] == "overshot"

    # ----- Edge cases (architect3 §4.3 cases 5-7) -----------------------

    def test_feature_variety_none(self):
        """Feature variety None → surfaced=False, reason=feature_variety_missing.
        Stats still computed over the stamped dispatches."""
        result = compute_variety_divergence(None, [5, 5, 5])
        assert result["surfaced"] is False
        assert result["direction"] is None
        assert result["delta"] is None
        assert result["reason"] == "feature_variety_missing"
        assert result["mean"] == 5

    def test_empty_dispatches(self):
        """Empty dispatch list → surfaced=False, reason=no_dispatches_stamped,
        coverage=0.0, all stats None."""
        result = compute_variety_divergence(9, [])
        assert result["surfaced"] is False
        assert result["coverage"] == 0.0
        assert result["mean"] is None
        assert result["max"] is None
        assert result["min"] is None
        assert result["reason"] == "no_dispatches_stamped"

    def test_mixed_coverage(self):
        """3 stamped + 2 unstamped → coverage=0.6; math over the 3 stamped.
        Per D8 partial-corpus handling."""
        result = compute_variety_divergence(
            9, [9, 9, 9], total_pact_dispatch_count=5,
        )
        assert result["coverage"] == 0.6
        assert result["mean"] == 9
        assert result["delta"] == 0
        assert result["surfaced"] is False

    def test_total_count_zero_with_stamps_trips_advisory(self):
        """A COMPUTED total_pact_dispatch_count=0 with stamps firing is the
        WORST denominator collapse (every dispatch marker absent while
        variety stamps exist). It trips the coverage_exceeds_unity advisory
        rather than fail-opening to coverage=1.0 (which would HIDE the
        regression). surfaced stays False (a divergence over a broken
        denominator is untrustworthy). coverage is a FINITE >=1.0 signal
        (the stamped count, denominator-treated-as-1) — not +inf — to
        avoid an inf footgun downstream; it is debug-only here. Contrast
        None / negative,
        which DO fail-open (see test_total_count_negative_falls_back) — a
        negative count is impossible/garbage, not a meaningful collapse."""
        result = compute_variety_divergence(
            8, [8, 8], total_pact_dispatch_count=0,
        )
        # stamped == 2 → finite stamped-count signal 2.0 (>=1.0), not +inf
        assert result["coverage"] == 2.0
        assert result["reason"] == "coverage_exceeds_unity"
        assert result["surfaced"] is False

    def test_total_count_negative_falls_back(self):
        """total_pact_dispatch_count negative (defensive against caller
        bugs) falls back to the all-stamped assumption."""
        result = compute_variety_divergence(
            8, [8, 8], total_pact_dispatch_count=-1,
        )
        assert result["coverage"] == 1.0

    # ----- Threshold parameterization -----------------------------------

    def test_custom_threshold_loosened(self):
        """threshold=3 — delta=2 is NOT surfaced (delta < threshold)."""
        result = compute_variety_divergence(10, [8, 8, 8], threshold=3)
        assert result["surfaced"] is False
        assert result["delta"] == 2

    def test_custom_threshold_tightened(self):
        """threshold=1 — delta=1 IS surfaced. The knob per §6.2 lets
        future calibration loosen or tighten without code change."""
        result = compute_variety_divergence(9, [8, 8, 8], threshold=1)
        assert result["surfaced"] is True
        assert result["delta"] == 1
        assert result["direction"] == "overshot"

    def test_default_threshold_constant(self):
        """DEFAULT_THRESHOLD is exposed and equals 2. Pins the
        SSOT-via-import discipline (no hard-coded 2 in test code)."""
        assert DEFAULT_THRESHOLD == 2

    # ----- Stable-key contract ------------------------------------------

    def test_return_dict_has_stable_keys(self):
        """Every return path returns the SAME 8 keys; downstream LLM-prose
        composer in wrap-up.md §4 reads them by name. Counter-pin against
        a future refactor that adds variant keys per branch."""
        expected_keys = {
            "coverage", "mean", "max", "min",
            "delta", "surfaced", "direction", "reason",
        }
        for args in (
            (9, [5, 5, 5, 5, 5]),       # overshoot
            (5, [8, 8, 8]),             # undershoot
            (8, [7, 8, 9]),             # within
            (None, [5, 5, 5]),          # feature missing
            (9, []),                    # empty
            (9, [9, 9], 5),             # mixed coverage
        ):
            result = compute_variety_divergence(*args)
            assert set(result.keys()) == expected_keys, (
                f"key set drift on args {args}: {set(result.keys())}"
            )

    def test_surfaced_direction_pairing(self):
        """When surfaced=True, direction is "overshot" or "undershot",
        never None. When surfaced=False, direction is None. Pins the
        boolean-pair semantic the lead carry-forward affirmed."""
        for feature, dispatches in ((9, [5, 5, 5]), (5, [9, 9, 9])):
            result = compute_variety_divergence(feature, dispatches)
            assert result["surfaced"] is True
            assert result["direction"] in ("overshot", "undershot")
        for feature, dispatches in (
            (8, [7, 8, 9]),         # within
            (None, [5, 5, 5]),      # feature missing
            (9, []),                # empty
        ):
            result = compute_variety_divergence(feature, dispatches)
            assert result["surfaced"] is False
            assert result["direction"] is None
