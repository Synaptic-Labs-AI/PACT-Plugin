"""
Tests for orchestration retrospective in wrap-up.md.

Tests cover:
1. Orchestration Retrospective section exists
2. Four assessment questions defined
3. pact-memory save convention documented
4. Estimation pattern question documented
"""
from pathlib import Path

import pytest


WRAPUP_PATH = Path(__file__).parent.parent / "commands" / "wrap-up.md"


class TestOrchestrationRetrospective:
    """Tests for orchestration retrospective in wrap-up command."""

    @pytest.fixture
    def wrapup_content(self):
        return WRAPUP_PATH.read_text(encoding="utf-8")

    def test_retrospective_section_exists(self, wrapup_content):
        assert "Orchestration Retrospective" in wrapup_content

    def test_variety_accuracy_question(self, wrapup_content):
        assert "Variety accuracy" in wrapup_content

    def test_phase_efficiency_question(self, wrapup_content):
        assert "Phase efficiency" in wrapup_content

    def test_specialist_fit_question(self, wrapup_content):
        assert "Specialist fit" in wrapup_content

    def test_estimation_pattern_question(self, wrapup_content):
        assert "Estimation pattern" in wrapup_content

    def test_memory_save_convention(self, wrapup_content):
        assert "orchestration_calibration" in wrapup_content


def _question_line(content, prefix):
    """Return the single wrap-up.md line beginning with `prefix` (each
    retrospective question is one long markdown line). Asserting WITHIN the
    specific question line is stronger than a whole-file substring grep."""
    for line in content.splitlines():
        if line.lstrip().startswith(prefix):
            return line
    raise AssertionError(f"question line not found for prefix {prefix!r}")


class TestRetroReadHardening:
    """#966 (docs-only): wrap-up Q5/Q6 journal-read guidance hardening — the
    explicit single-JSON-array parse, the output-masking bans, the Q5
    masked-empty re-read guard, and the Q6 fallback-on-zero that prevents a
    fabricated 0% signal rate from a masked/crashed read.

    These are STRUCTURAL (prose) assertions: they detect REMOVAL or
    rewording-away of the hardening instruction, but cannot prove an LLM
    obeys it at runtime — a known vacuity limit of prose tests, documented
    as a residual risk in the TEST HANDOFF. They are the appropriate
    coverage for a docs-only change whose logic is not extracted to code.
    """

    @pytest.fixture
    def wrapup_content(self):
        return WRAPUP_PATH.read_text(encoding="utf-8")

    @pytest.fixture
    def q5(self, wrapup_content):
        return _question_line(wrapup_content, "5. **Variety divergence**")

    @pytest.fixture
    def q6(self, wrapup_content):
        return _question_line(wrapup_content, "6. **Variety acknowledgment signals**")

    # --- Q5 read-mechanics hardening ---

    def test_q5_states_single_json_array_parse(self, q5):
        assert "SINGLE JSON array" in q5
        assert "json.loads(output)" in q5

    def test_q5_warns_against_line_by_line_parse(self, q5):
        assert "do NOT parse line-by-line" in q5

    def test_q5_bans_output_masking_pipes(self, q5):
        # the three constructs that mask a parse crash as emptiness
        assert "2>/dev/null" in q5
        assert "|| echo" in q5
        assert "`head`" in q5

    def test_q5_masked_empty_guard_precedes_predates_conclusion(self, q5):
        # the re-read guard must appear, and gate the "pre-dates" conclusion
        assert "masked-empty guard" in q5
        assert "re-read the raw" in q5
        assert "session-journal.jsonl" in q5
        # the unique OMISSION-note phrasing (distinct from the earlier
        # GC-problem mention "wrongly reports 'pre-dates stamping'")
        assert "pre-dates per-dispatch stamping" in q5
        # ordering: the guard must precede the pre-dates OMISSION conclusion
        assert q5.index("masked-empty guard") < q5.index(
            "pre-dates per-dispatch stamping"
        )

    def test_q5_denominator_uses_count_helper_not_len_agent_dispatch(self, q5):
        assert "count_task_b_dispatch_sites" in q5

    def test_q5_arc_scoped_with_since(self, q5):
        assert "Arc scope (current feature only)" in q5
        assert "--since" in q5

    # --- Q6 structural safety (no explicit re-read guard; rests on the
    #     parse bans + fallback-on-zero) ---

    def test_q6_states_single_json_array_parse(self, q6):
        assert "SINGLE JSON array" in q6
        assert "json.loads(output)" in q6

    def test_q6_bans_output_masking_pipes(self, q6):
        assert "2>/dev/null" in q6
        assert "|| echo" in q6
        assert "`head`" in q6

    def test_q6_fallback_on_zero_prevents_fabricated_signal_rate(self, q6):
        """The structural guarantee that a masked/crashed read (which yields
        zero teachback_ack events) cannot be reported as a real 0% signal
        rate: Q6 falls back to the task store ONLY when the journal yields
        zero teachback_ack events (#966 noted a masked read corrupting Q6 to
        0% vs a true 44%)."""
        assert "zero `teachback_ack` events" in q6
        assert "fall back to the legacy" in q6

    def test_q6_arc_scoped_with_since(self, q6):
        assert "--since" in q6
