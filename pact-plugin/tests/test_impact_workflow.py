"""
Tests for imPACT workflow support in the compaction refresh system.

Tests workflow detection patterns, step markers, termination signals,
and prose template generation for the imPACT (triage when blocked) workflow.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from refresh.patterns import (
    TRIGGER_PATTERNS,
    STEP_MARKERS,
    TERMINATION_SIGNALS,
    WORKFLOW_PATTERNS,
    compile_workflow_patterns,
    is_termination_signal,
)
from refresh.shared_constants import (
    STEP_DESCRIPTIONS,
    PROSE_CONTEXT_TEMPLATES,
)


class TestImPACTTriggerPattern:
    """Tests for imPACT workflow trigger pattern detection."""

    def test_trigger_pattern_exists(self):
        """Test that imPACT trigger pattern is defined."""
        assert "imPACT" in TRIGGER_PATTERNS

    def test_trigger_pattern_matches_basic(self):
        """Test basic imPACT trigger matching."""
        pattern = TRIGGER_PATTERNS["imPACT"]
        assert pattern.search("/PACT:imPACT") is not None

    def test_trigger_pattern_case_insensitive(self):
        """Test trigger pattern is case insensitive."""
        pattern = TRIGGER_PATTERNS["imPACT"]
        assert pattern.search("/PACT:IMPACT") is not None
        assert pattern.search("/pact:impact") is not None
        assert pattern.search("/Pact:ImPact") is not None

    def test_trigger_pattern_with_arguments(self):
        """Test trigger pattern matches with arguments."""
        pattern = TRIGGER_PATTERNS["imPACT"]
        assert pattern.search("/PACT:imPACT blocker report here") is not None
        assert pattern.search("/PACT:imPACT: auth middleware failing") is not None

    def test_trigger_pattern_with_surrounding_text(self):
        """Test trigger pattern matches within surrounding text."""
        pattern = TRIGGER_PATTERNS["imPACT"]
        assert pattern.search("Running /PACT:imPACT now") is not None
        assert pattern.search(">>> /PACT:imPACT <<<") is not None


class TestImPACTStepMarkers:
    """Tests for imPACT workflow step markers."""

    def test_step_markers_exist(self):
        """Test that imPACT step markers are defined."""
        assert "imPACT" in STEP_MARKERS

    def test_step_markers_contains_triage(self):
        """Test triage step is present."""
        assert "triage" in STEP_MARKERS["imPACT"]

    def test_step_markers_contains_assessing_redo(self):
        """Test assessing-redo step is present."""
        assert "assessing-redo" in STEP_MARKERS["imPACT"]

    def test_step_markers_contains_selecting_agents(self):
        """Test selecting-agents step is present."""
        assert "selecting-agents" in STEP_MARKERS["imPACT"]

    def test_step_markers_contains_resolution_path(self):
        """Test resolution-path step is present."""
        assert "resolution-path" in STEP_MARKERS["imPACT"]

    def test_step_markers_count(self):
        """Test imPACT has expected number of steps."""
        assert len(STEP_MARKERS["imPACT"]) == 4


class TestImPACTTerminationSignals:
    """Tests for imPACT workflow termination signal detection."""

    def test_termination_signals_exist(self):
        """Test that imPACT termination signals are defined."""
        assert "imPACT" in TERMINATION_SIGNALS

    def test_redo_solo_signal(self):
        """Test detecting redo solo outcome."""
        assert is_termination_signal("Outcome: redo solo", "imPACT") is True
        assert is_termination_signal("Decision: redo solo the PREPARE phase", "imPACT") is True

    def test_redo_with_help_signal(self):
        """Test detecting redo with help outcome."""
        assert is_termination_signal("Outcome: redo with help", "imPACT") is True
        assert is_termination_signal("Decision: redo with help from pact-architect", "imPACT") is True

    def test_proceed_with_help_signal(self):
        """Test detecting proceed with help outcome."""
        assert is_termination_signal("Outcome: proceed with help", "imPACT") is True
        assert is_termination_signal("Decision: proceed with help from specialists", "imPACT") is True

    def test_impact_resolved_signal(self):
        """Test detecting imPACT resolved signal."""
        assert is_termination_signal("imPACT resolved. Continuing workflow.", "imPACT") is True

    def test_returning_to_workflow_signal(self):
        """Test detecting returning to workflow signal."""
        assert is_termination_signal("Returning to workflow", "imPACT") is True
        assert is_termination_signal("Returning to main workflow", "imPACT") is True

    def test_blocker_resolved_signal(self):
        """Test detecting blocker resolved signal."""
        assert is_termination_signal("Blocker resolved. Moving forward.", "imPACT") is True

    # --- v3.5.0 outcome signals (anchored patterns) ---

    def test_redo_prior_phase_signal(self):
        """Test detecting v3.5.0 'redo prior phase' outcome."""
        assert is_termination_signal("Outcome: redo prior phase", "imPACT") is True
        assert is_termination_signal("> redo prior phase", "imPACT") is True
        assert is_termination_signal("- redo prior phase", "imPACT") is True

    def test_augment_present_phase_signal(self):
        """Test detecting v3.5.0 'augment present phase' outcome."""
        assert is_termination_signal("Outcome: augment present phase", "imPACT") is True
        assert is_termination_signal("> augment present phase", "imPACT") is True

    def test_invoke_repact_signal(self):
        """Test detecting v3.5.0 'invoke rePACT' outcome."""
        assert is_termination_signal("Outcome: invoke rePACT", "imPACT") is True
        assert is_termination_signal("> invoke rePACT for nested cycle", "imPACT") is True

    def test_terminate_agent_signal(self):
        """Test detecting v3.5.0 'terminate agent' outcome."""
        assert is_termination_signal("Outcome: terminate agent", "imPACT") is True
        assert is_termination_signal("> terminate agent — context exhausted", "imPACT") is True

    def test_not_truly_blocked_signal(self):
        """Test detecting v3.5.0 'not truly blocked' outcome."""
        assert is_termination_signal("Outcome: not truly blocked", "imPACT") is True
        assert is_termination_signal("> not truly blocked, continue", "imPACT") is True

    def test_escalate_to_user_signal(self):
        """Test detecting v3.5.0 'escalate to user' outcome."""
        assert is_termination_signal("Outcome: escalate to user", "imPACT") is True
        assert is_termination_signal("> escalate to user for input", "imPACT") is True

    def test_no_termination_during_triage(self):
        """Test non-termination content during triage."""
        assert is_termination_signal("Analyzing the blocker...", "imPACT") is False
        assert is_termination_signal("Assessing whether to redo prior phase", "imPACT") is False

    def test_v350_casual_mentions_do_not_match(self):
        """Test that casual mid-sentence mentions of v3.5.0 outcomes don't false-match."""
        assert is_termination_signal("Need to augment present phase approach", "imPACT") is False
        assert is_termination_signal("Should we invoke rePACT here?", "imPACT") is False
        assert is_termination_signal("We may need to terminate agent soon", "imPACT") is False
        assert is_termination_signal("The agent is not truly blocked yet", "imPACT") is False
        assert is_termination_signal("We could escalate to user if needed", "imPACT") is False
        assert is_termination_signal("Considering whether to redo prior phase", "imPACT") is False

    def test_case_insensitive_termination(self):
        """Test termination signals are case insensitive."""
        assert is_termination_signal("REDO SOLO", "imPACT") is True
        assert is_termination_signal("Blocker Resolved", "imPACT") is True


class TestImPACTWorkflowPattern:
    """Tests for imPACT workflow pattern compilation."""

    def test_impact_in_workflow_patterns(self):
        """Test imPACT is in WORKFLOW_PATTERNS."""
        assert "imPACT" in WORKFLOW_PATTERNS

    def test_compiled_pattern_has_correct_name(self):
        """Test compiled pattern has correct name."""
        pattern = WORKFLOW_PATTERNS["imPACT"]
        assert pattern.name == "imPACT"

    def test_compiled_pattern_has_trigger(self):
        """Test compiled pattern has trigger pattern."""
        pattern = WORKFLOW_PATTERNS["imPACT"]
        assert pattern.trigger_pattern is not None
        assert pattern.trigger_pattern.search("/PACT:imPACT") is not None

    def test_compiled_pattern_has_step_markers(self):
        """Test compiled pattern has step markers."""
        pattern = WORKFLOW_PATTERNS["imPACT"]
        assert len(pattern.step_markers) == 4
        assert "triage" in pattern.step_markers

    def test_compiled_pattern_has_termination_signals(self):
        """Test compiled pattern has termination signals (6 v3.5.0 + 6 v3.4 compat)."""
        pattern = WORKFLOW_PATTERNS["imPACT"]
        assert len(pattern.termination_signals) == 12

    def test_compile_workflow_patterns_includes_impact(self):
        """Test compile_workflow_patterns function includes imPACT."""
        patterns = compile_workflow_patterns()
        assert "imPACT" in patterns


class TestImPACTStepDescriptions:
    """Tests for imPACT step descriptions."""

    def test_triage_description(self):
        """Test triage step description exists."""
        assert "triage" in STEP_DESCRIPTIONS
        assert "blocker" in STEP_DESCRIPTIONS["triage"].lower()

    def test_assessing_redo_description(self):
        """Test assessing-redo step description exists."""
        assert "assessing-redo" in STEP_DESCRIPTIONS
        assert "redo" in STEP_DESCRIPTIONS["assessing-redo"].lower()

    def test_selecting_agents_description(self):
        """Test selecting-agents step description exists."""
        assert "selecting-agents" in STEP_DESCRIPTIONS
        assert "agent" in STEP_DESCRIPTIONS["selecting-agents"].lower()

    def test_resolution_path_description(self):
        """Test resolution-path step description exists."""
        assert "resolution-path" in STEP_DESCRIPTIONS
        assert "resolution" in STEP_DESCRIPTIONS["resolution-path"].lower()


class TestImPACTProseTemplates:
    """Tests for imPACT prose template functions."""

    def test_triage_template_exists(self):
        """Test triage prose template exists."""
        assert "triage" in PROSE_CONTEXT_TEMPLATES

    def test_triage_template_basic(self):
        """Test triage template with no context."""
        template_fn = PROSE_CONTEXT_TEMPLATES["triage"]
        result = template_fn({})
        assert "triaging" in result.lower()
        assert "blocker" in result.lower()

    def test_triage_template_with_blocker(self):
        """Test triage template with blocker context."""
        template_fn = PROSE_CONTEXT_TEMPLATES["triage"]
        result = template_fn({"blocker": "auth middleware failing"})
        assert "auth middleware failing" in result

    def test_assessing_redo_template_exists(self):
        """Test assessing-redo prose template exists."""
        assert "assessing-redo" in PROSE_CONTEXT_TEMPLATES

    def test_assessing_redo_template_basic(self):
        """Test assessing-redo template with no context."""
        template_fn = PROSE_CONTEXT_TEMPLATES["assessing-redo"]
        result = template_fn({})
        assert "redo" in result.lower()
        assert "prior phase" in result.lower()

    def test_assessing_redo_template_with_phase(self):
        """Test assessing-redo template with prior phase context."""
        template_fn = PROSE_CONTEXT_TEMPLATES["assessing-redo"]
        result = template_fn({"prior_phase": "PREPARE"})
        assert "PREPARE" in result

    def test_selecting_agents_template_exists(self):
        """Test selecting-agents prose template exists."""
        assert "selecting-agents" in PROSE_CONTEXT_TEMPLATES

    def test_selecting_agents_template_basic(self):
        """Test selecting-agents template with no context."""
        template_fn = PROSE_CONTEXT_TEMPLATES["selecting-agents"]
        result = template_fn({})
        assert "agent" in result.lower()

    def test_selecting_agents_template_with_agents(self):
        """Test selecting-agents template with agents context."""
        template_fn = PROSE_CONTEXT_TEMPLATES["selecting-agents"]
        result = template_fn({"agents": "pact-backend-coder, pact-architect"})
        assert "pact-backend-coder" in result

    def test_resolution_path_template_exists(self):
        """Test resolution-path prose template exists."""
        assert "resolution-path" in PROSE_CONTEXT_TEMPLATES

    def test_resolution_path_template_basic(self):
        """Test resolution-path template with no context."""
        template_fn = PROSE_CONTEXT_TEMPLATES["resolution-path"]
        result = template_fn({})
        assert "resolution" in result.lower()

    # --- v3.5.0 outcome prose templates ---

    def test_resolution_path_template_redo_prior_phase(self):
        """Test resolution-path template with v3.5.0 redo_prior_phase outcome."""
        template_fn = PROSE_CONTEXT_TEMPLATES["resolution-path"]
        result = template_fn({"outcome": "redo_prior_phase"})
        assert "redo prior phase" in result.lower()

    def test_resolution_path_template_augment_present_phase(self):
        """Test resolution-path template with v3.5.0 augment_present_phase outcome."""
        template_fn = PROSE_CONTEXT_TEMPLATES["resolution-path"]
        result = template_fn({"outcome": "augment_present_phase"})
        assert "augment" in result.lower()

    def test_resolution_path_template_invoke_repact(self):
        """Test resolution-path template with v3.5.0 invoke_repact outcome."""
        template_fn = PROSE_CONTEXT_TEMPLATES["resolution-path"]
        result = template_fn({"outcome": "invoke_repact"})
        assert "repact" in result.lower()

    def test_resolution_path_template_terminate_agent(self):
        """Test resolution-path template with v3.5.0 terminate_agent outcome."""
        template_fn = PROSE_CONTEXT_TEMPLATES["resolution-path"]
        result = template_fn({"outcome": "terminate_agent"})
        assert "terminate" in result.lower()

    def test_resolution_path_template_not_truly_blocked(self):
        """Test resolution-path template with v3.5.0 not_truly_blocked outcome."""
        template_fn = PROSE_CONTEXT_TEMPLATES["resolution-path"]
        result = template_fn({"outcome": "not_truly_blocked"})
        assert "not truly blocked" in result.lower()

    def test_resolution_path_template_escalate_to_user(self):
        """Test resolution-path template with v3.5.0 escalate_to_user outcome."""
        template_fn = PROSE_CONTEXT_TEMPLATES["resolution-path"]
        result = template_fn({"outcome": "escalate_to_user"})
        assert "escalate" in result.lower()

    # --- v3.4 outcome prose templates (backwards compat) ---

    def test_resolution_path_template_redo_solo(self):
        """Test resolution-path template with redo_solo outcome."""
        template_fn = PROSE_CONTEXT_TEMPLATES["resolution-path"]
        result = template_fn({"outcome": "redo_solo"})
        assert "redo prior phase solo" in result.lower()

    def test_resolution_path_template_redo_with_help(self):
        """Test resolution-path template with redo_with_help outcome."""
        template_fn = PROSE_CONTEXT_TEMPLATES["resolution-path"]
        result = template_fn({"outcome": "redo_with_help"})
        assert "redo prior phase with agent assistance" in result.lower()

    def test_resolution_path_template_proceed_with_help(self):
        """Test resolution-path template with proceed_with_help outcome."""
        template_fn = PROSE_CONTEXT_TEMPLATES["resolution-path"]
        result = template_fn({"outcome": "proceed_with_help"})
        assert "proceed with agent assistance" in result.lower()


class TestImPACTConsistency:
    """Tests for consistency between imPACT patterns and templates."""

    def test_all_step_markers_have_descriptions(self):
        """Test all imPACT step markers have descriptions."""
        for step in STEP_MARKERS["imPACT"]:
            assert step in STEP_DESCRIPTIONS, f"Missing description for step: {step}"

    def test_all_step_markers_have_prose_templates(self):
        """Test all imPACT step markers have prose templates."""
        for step in STEP_MARKERS["imPACT"]:
            assert step in PROSE_CONTEXT_TEMPLATES, f"Missing prose template for step: {step}"

    def test_all_workflows_have_impact(self):
        """Test imPACT is consistently present across all pattern structures."""
        assert "imPACT" in TRIGGER_PATTERNS
        assert "imPACT" in STEP_MARKERS
        assert "imPACT" in TERMINATION_SIGNALS
        assert "imPACT" in WORKFLOW_PATTERNS
