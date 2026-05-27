"""Tests for the optional reasoning_reconstruction field on metadata.teachback_submit.

Pins the schema and variety-band gate for the L1.5 method-level
extension. Coverage:

  - Schema: optional parent key (absent / null both valid)
  - Schema: when present, must be dict with exactly 3 string keys
  - Schema: each sub-key must be non-empty string
  - Variety trigger: route_workflow(score) -> required / recommended / skipped
  - Variety trigger: constants imported from variety_scorer.py (NOT literals)
  - Variety None-default: treated as ROUTE_ORCHESTRATE band (Recommended)
  - Exemption: TEACHBACK_EXEMPT_AGENT_TYPES bypass also bypasses reasoning_reconstruction
  - Ordering invariant: Step 1->2->3 unchanged by 5-field shape (regression pin)
  - Backward-compat: 4-field shape (without reasoning_reconstruction) validates
  - Internal consistency: lead-side check accepts well-formed triangle;
    rejects partial / empty.

The schema sub-key validator now lives in shared/teachback_schema.py
(SSOT). This module's `_validate_reasoning_reconstruction` wraps the
shared validator with the lead-side band-routing logic that combines
sub-key validation with variety-band-driven missing-reconstruction
rejection. TEACHBACK_REQUIRED_SUBKEYS is imported from shared (canonical
3-tuple).
NOTE: the AST-scan in TestVarietyTrigger.test_constants_imported_not_literal
walks Path(__file__) only — if band thresholds (6 / 10 / 14) are migrated
out of variety_scorer.py into another module in a future PR, the scan
path must be extended to cover that module.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from shared.intentional_wait import TEACHBACK_EXEMPT_AGENT_TYPES
from shared.teachback_schema import (
    TEACHBACK_REQUIRED_SUBKEYS,
    validate_reasoning_reconstruction as _validate_subkeys,
)
from shared.variety_scorer import (
    COMPACT_MAX,
    ORCHESTRATE_MAX,
    PLAN_MODE_MAX,
    ROUTE_COMPACT,
    ROUTE_ORCHESTRATE,
    ROUTE_PLAN_MODE,
    ROUTE_RESEARCH_SPIKE,
    route_workflow,
)


# ============================================================================
# Schema validator under test
# ============================================================================


def _validate_reasoning_reconstruction(
    teachback_submit: dict, dispatching_variety_score: int | None
) -> tuple[str, str | None]:
    """Lead-side schema gate for reasoning_reconstruction.

    Returns ("accept", None) on pass, or ("reject", reason) on fail.
    Combines band-routing (missing-at-required-band rejection) with the
    pure sub-key validator from shared/teachback_schema.py.
    """
    reconstruction = teachback_submit.get("reasoning_reconstruction")
    band = _band_for_variety(dispatching_variety_score)

    if reconstruction is None:
        if band in (ROUTE_PLAN_MODE, ROUTE_RESEARCH_SPIKE):
            return "reject", "missing_reasoning_reconstruction"
        return "accept", None

    subkey_problem = _validate_subkeys(reconstruction)
    if subkey_problem:
        return "reject", subkey_problem
    return "accept", None


def _band_for_variety(score: int | None) -> str:
    """Route a variety score to its workflow band, treating None / non-int
    as the Recommended (ROUTE_ORCHESTRATE) band per architect §2.3.
    """
    if not isinstance(score, int):
        return ROUTE_ORCHESTRATE
    return route_workflow(score)


# ============================================================================
# Fixtures
# ============================================================================


def _payload(reconstruction: dict | None = None) -> dict:
    """Build a 4-field teachback payload; optionally include a 5th
    reasoning_reconstruction sub-object. Used across tests so the four
    canonical fields stay byte-equal across test cases.
    """
    payload: dict = {
        "understanding": "build the X with constraint Y",
        "most_likely_wrong": "interface Z naming",
        "least_confident_item": "Z must accept either A or B",
        "first_action": "read docs/architecture/X.md in full",
    }
    if reconstruction is not None:
        payload["reasoning_reconstruction"] = reconstruction
    return payload


def _well_formed_triangle() -> dict:
    return {
        "decision_attribution": (
            "I understand the architect chose nested 5th field "
            "because it preserves the whitelist invariant"
        ),
        "assumption_trace": (
            "This depends on (a) teachback_submit being whitelisted, "
            "and (b) variety_scorer constants being stable"
        ),
        "contingency_clause": (
            "If teachback_submit is NOT whitelisted, scope expands to "
            "include the wake-lifecycle whitelist edit"
        ),
    }


# ============================================================================
# Test classes (architect §4.2 catalog)
# ============================================================================


class TestSchemaShape:
    """Optional parent key + 3-key triangle schema gate."""

    def test_field_absent_is_valid(self):
        verdict, reason = _validate_reasoning_reconstruction(_payload(), dispatching_variety_score=5)
        assert verdict == "accept"
        assert reason is None

    def test_field_null_is_valid(self):
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(reconstruction=None), dispatching_variety_score=5
        )
        assert verdict == "accept"
        assert reason is None

    def test_field_present_requires_3_keys(self):
        partial = {k: "ok" for k in TEACHBACK_REQUIRED_SUBKEYS[:2]}
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(reconstruction=partial), dispatching_variety_score=5
        )
        assert verdict == "reject"
        assert reason == "malformed_reasoning_reconstruction"

    def test_field_present_rejects_extra_keys(self):
        extra = {k: "ok" for k in TEACHBACK_REQUIRED_SUBKEYS}
        extra["surprise_key"] = "shouldnt be here"
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(reconstruction=extra), dispatching_variety_score=5
        )
        assert verdict == "reject"
        assert reason == "malformed_reasoning_reconstruction"

    def test_each_subkey_must_be_string(self):
        bad = dict.fromkeys(TEACHBACK_REQUIRED_SUBKEYS, "ok")
        bad["assumption_trace"] = ["list", "not", "string"]
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(reconstruction=bad), dispatching_variety_score=5
        )
        assert verdict == "reject"
        assert reason == "empty_reasoning_reconstruction_field"

    def test_each_subkey_must_be_nonempty(self):
        bad = dict.fromkeys(TEACHBACK_REQUIRED_SUBKEYS, "ok")
        bad["contingency_clause"] = "   "  # whitespace-only also rejected
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(reconstruction=bad), dispatching_variety_score=5
        )
        assert verdict == "reject"
        assert reason == "empty_reasoning_reconstruction_field"

    def test_field_present_not_dict_rejected(self):
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(reconstruction="should be a dict"), dispatching_variety_score=5
        )
        assert verdict == "reject"
        assert reason == "malformed_reasoning_reconstruction"


class TestVarietyTrigger:
    """Variety-band gate: required / recommended / skipped per band."""

    def test_compact_band_skipped(self):
        # variety in COMPACT band — absence is accepted
        score = COMPACT_MAX  # boundary: last score still in the COMPACT band
        assert route_workflow(score) == ROUTE_COMPACT
        verdict, _ = _validate_reasoning_reconstruction(
            _payload(), dispatching_variety_score=score
        )
        assert verdict == "accept"

    def test_orchestrate_band_recommended(self):
        # variety in ORCHESTRATE band — absence is accepted (recommended-not-required)
        score = ORCHESTRATE_MAX
        assert route_workflow(score) == ROUTE_ORCHESTRATE
        verdict, _ = _validate_reasoning_reconstruction(
            _payload(), dispatching_variety_score=score
        )
        assert verdict == "accept"

    def test_plan_mode_band_required(self):
        # variety in PLAN_MODE band — absence triggers rejection
        score = PLAN_MODE_MAX
        assert route_workflow(score) == ROUTE_PLAN_MODE
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(), dispatching_variety_score=score
        )
        assert verdict == "reject"
        assert reason == "missing_reasoning_reconstruction"

    def test_research_spike_band_required(self):
        # variety above PLAN_MODE_MAX — research-spike band; same Required behavior
        score = PLAN_MODE_MAX + 1
        assert route_workflow(score) == ROUTE_RESEARCH_SPIKE
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(), dispatching_variety_score=score
        )
        assert verdict == "reject"
        assert reason == "missing_reasoning_reconstruction"

    def test_none_variety_treated_as_orchestrate(self):
        # None / missing variety score — treated as ROUTE_ORCHESTRATE (Recommended)
        verdict, _ = _validate_reasoning_reconstruction(
            _payload(), dispatching_variety_score=None
        )
        assert verdict == "accept"

    def test_non_int_variety_treated_as_orchestrate(self):
        # Non-int (string, float, etc.) — same transitional permissiveness as None
        verdict, _ = _validate_reasoning_reconstruction(
            _payload(), dispatching_variety_score="not an int"  # type: ignore[arg-type]
        )
        assert verdict == "accept"

    def test_band_for_variety_round_trips_through_ssot(self):
        # Sanity: _band_for_variety must match route_workflow exactly for integer inputs
        for score in (COMPACT_MAX, ORCHESTRATE_MAX, PLAN_MODE_MAX, PLAN_MODE_MAX + 2):
            assert _band_for_variety(score) == route_workflow(score)

    def test_constants_imported_not_literal(self):
        """AST-scan THIS module: no hard-coded threshold literals.

        Architect §2.2: the variety thresholds MUST come from variety_scorer.py
        constants. Test code dereferences COMPACT_MAX / ORCHESTRATE_MAX /
        PLAN_MODE_MAX, never their literal values, so the test module is
        decoupled from any future re-tier of the bands.

        The forbidden set is built from the SSOT constants themselves, so
        the scan does not need to embed the literal values (which would
        be self-detecting). If the SSOT bumps thresholds, this scan auto-
        re-targets without test edits.
        """
        forbidden = {COMPACT_MAX, ORCHESTRATE_MAX, PLAN_MODE_MAX}
        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        hits: list[tuple[int, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, int):
                if node.value in forbidden:
                    hits.append((node.lineno, node.value))

        assert not hits, (
            f"Hard-coded variety threshold literals found in test module: "
            f"{hits}. Use COMPACT_MAX / ORCHESTRATE_MAX / PLAN_MODE_MAX "
            f"from shared.variety_scorer instead. Architect §2.2."
        )


class TestExemption:
    """TEACHBACK_EXEMPT_AGENT_TYPES bypass covers method reconstruction too.

    Architect §2.4: the existing exemption (currently `{pact-secretary}`)
    covers method reconstruction transparently — no new carve-out logic
    is introduced. These tests pin the exemption set membership; the
    predicate `is_teachback_exempt` itself is exercised by
    test_intentional_wait.py against the full team-config lookup surface.
    """

    def test_pact_secretary_is_exempt_agent_type(self):
        assert "pact-secretary" in TEACHBACK_EXEMPT_AGENT_TYPES

    def test_non_exempt_agent_types_excluded(self):
        # Sanity counterpart: typical PACT specialist agentTypes are NOT
        # in the exemption set; they remain subject to the teachback gate
        # (including the L1.5 sub-field at variety ≥ 11).
        for agent_type in ("backend-coder", "pact-devops-engineer", "pact-orchestrator"):
            assert agent_type not in TEACHBACK_EXEMPT_AGENT_TYPES


class TestOrderingInvariantRegression:
    """Pin the structural ordering invariant against the 5-field shape."""

    SKILL_FILE = Path(__file__).parent.parent / "skills" / "pact-teachback" / "SKILL.md"

    def test_step_ordering_unchanged_by_5_field_shape(self):
        """The Step 1 -> Step 2 -> Step 3 ordering prose in
        pact-teachback/SKILL.md is field-count-agnostic. Adding a nested
        sub-object inside the Step 1 JSON payload MUST NOT introduce a
        second metadata write, a re-ordered step sequence, or a second
        "Ordering invariant" phrase anchor. See architect §5.2.

        This pin co-defends EXPECTED_COUNTS in test_skills_structure.py.
        """
        text = self.SKILL_FILE.read_text(encoding="utf-8")
        # Phrase count is exactly 1 (matches test_skills_structure.py
        # EXPECTED_COUNTS pin).
        assert text.count("Ordering invariant") == 1
        # The three step markers appear in order.
        idx_step1 = text.index("**Step 1 ")
        idx_step2 = text.index("**Step 2 ")
        idx_step3 = text.index("**Step 3 ")
        assert idx_step1 < idx_step2 < idx_step3


class TestBackwardCompat:
    """4-field shape (no reasoning_reconstruction) continues to validate."""

    def test_4_field_payload_validates_without_reconstruction(self):
        # No 5th field; any variety band at or below ORCHESTRATE accepts.
        for score in (COMPACT_MAX, ORCHESTRATE_MAX, None):
            verdict, _ = _validate_reasoning_reconstruction(
                _payload(), dispatching_variety_score=score
            )
            assert verdict == "accept", f"4-field payload rejected at variety {score}"

    def test_existing_test_skills_structure_expected_counts_unchanged(self):
        """The EXPECTED_COUNTS pin (1 for pact-teachback, 3 for
        pact-agent-teams) is documented as architect §4.3 no-touch.
        Read it via the live test module and assert the values stay
        unchanged. If a future PR shifts these, this test fails first
        and surfaces the violation explicitly.
        """
        from test_skills_structure import TestOrderingInvariantPhraseCount

        counts = TestOrderingInvariantPhraseCount.EXPECTED_COUNTS
        assert counts["pact-teachback/SKILL.md"] == 1
        assert counts["pact-agent-teams/SKILL.md"] == 3


class TestLeadSideConsistencyCheck:
    """Schema-gate happy paths + targeted reject cases.

    Architect §3.2 (a)(b)(c) internal-consistency prompts are
    judgment-only — NOT exercised by automated tests in this PR; per
    §7.4 and the coder-discretion lean accepted during teachback gate review.
    Future PostToolUse hook escalation MAY add semantic probes.
    """

    def test_well_formed_triangle_accepted_at_required_band(self):
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(reconstruction=_well_formed_triangle()),
            dispatching_variety_score=PLAN_MODE_MAX,
        )
        assert verdict == "accept"
        assert reason is None

    def test_well_formed_triangle_accepted_at_recommended_band(self):
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(reconstruction=_well_formed_triangle()),
            dispatching_variety_score=ORCHESTRATE_MAX,
        )
        assert verdict == "accept"
        assert reason is None

    def test_missing_at_plan_mode_rejected_with_specific_reason(self):
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(),
            dispatching_variety_score=PLAN_MODE_MAX,
        )
        assert verdict == "reject"
        assert reason == "missing_reasoning_reconstruction"

    def test_malformed_at_any_band_rejected_with_specific_reason(self):
        bad = {"only_one_key": "value"}
        for score in (COMPACT_MAX, ORCHESTRATE_MAX, PLAN_MODE_MAX, PLAN_MODE_MAX + 2):
            verdict, reason = _validate_reasoning_reconstruction(
                _payload(reconstruction=bad), dispatching_variety_score=score
            )
            assert verdict == "reject", f"band {score} accepted malformed payload"
            assert reason == "malformed_reasoning_reconstruction"

    def test_canonical_exemplar_round_trip_accepted_at_plan_mode(self):
        """Empirical-validation integration smoke: the canonical
        ORPHAN_TOKEN_MAX_AGE_SECONDS / TOKEN_TTL × 24 / 300s contingency
        exemplar MUST be accepted by the schema gate at the Required band.

        Source: pact-plugin/agents/pact-orchestrator.md §12
        Internal-consistency gate examples (committed SSOT). The triangle
        ENCODES the same exemplar as the protocol prose; not byte-equal
        (the case prose is expanded for test clarity). The point of this
        case (distinct from the abstract well-formed-triangle accepts
        above) is to prove the schema gate accepts the canonical exemplar
        that the protocol documentation cites.
        """
        canonical_exemplar = {
            "decision_attribution": (
                "I understand the architect chose ORPHAN_TOKEN_MAX_AGE_SECONDS = 86400 "
                "(24 hours) because the issue body states 'TOKEN_TTL × 24 ≈ 24 hours' — "
                "i.e., the architect wants the cleanup window to be 24× the per-token "
                "TTL to provide a stale-attack buffer well beyond normal token lifetime."
            ),
            "assumption_trace": (
                "This reasoning depends on (a) TOKEN_TTL being on the order of 1 hour "
                "(so 24× lands at the 24-hour magnitude the issue body cites), and "
                "(b) a 'stale-attack buffer' being the primary security objective for "
                "the cleanup helper (not disk hygiene, not session-scope containment)."
            ),
            "contingency_clause": (
                "If TOKEN_TTL is actually substantially smaller (e.g., 300s, the "
                "merge-guard token TTL I saw in passing while reading the file), the "
                "24× multiplier yields 2 hours, not 24 — in which case the security "
                "framing collapses and the cleanup magnitude should be re-derived from "
                "FIRST principles (disk hygiene, not stale-attack window)."
            ),
        }
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(reconstruction=canonical_exemplar),
            dispatching_variety_score=PLAN_MODE_MAX,
        )
        assert verdict == "accept"
        assert reason is None


# ============================================================================
# JSON round-trip smoke (the payload is real metadata that lands on disk
# via TaskUpdate(metadata=...) — confirm the well-formed triangle survives
# a JSON serialize/deserialize without shape loss).
# ============================================================================


def test_payload_json_round_trip_preserves_triangle():
    payload = _payload(reconstruction=_well_formed_triangle())
    serialized = json.dumps({"teachback_submit": payload})
    restored = json.loads(serialized)["teachback_submit"]
    assert set(restored["reasoning_reconstruction"].keys()) == set(TEACHBACK_REQUIRED_SUBKEYS)
    verdict, _ = _validate_reasoning_reconstruction(
        restored, dispatching_variety_score=PLAN_MODE_MAX
    )
    assert verdict == "accept"
