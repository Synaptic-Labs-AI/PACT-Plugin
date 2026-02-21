"""
Tests for shared_constants.py prose template functions.

Ensures all prose context template functions generate appropriate strings
for workflow step descriptions used in refresh messages.
"""

import sys
from pathlib import Path

import pytest

# Add hooks directory to path for refresh package imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from refresh.shared_constants import (
    STEP_DESCRIPTIONS,
    PROSE_CONTEXT_TEMPLATES,
    # peer-review steps
    _prose_commit,
    _prose_create_pr,
    _prose_invoke_reviewers,
    _prose_synthesize,
    _prose_recommendations,
    _prose_merge_ready,
    _prose_awaiting_user_decision,
    # orchestrate steps
    _prose_variety_assess,
    _prose_prepare,
    _prose_architect,
    _prose_code,
    _prose_test,
    # plan-mode steps
    _prose_analyze,
    _prose_consult,
    _prose_present,
    # comPACT steps
    _prose_invoking_specialist,
    _prose_specialist_completed,
    # rePACT steps
    _prose_nested_prepare,
    _prose_nested_architect,
    _prose_nested_code,
    _prose_nested_test,
    # imPACT steps
    _prose_triage,
    _prose_assessing_redo,
    _prose_selecting_agents,
    _prose_resolution_path,
)


class TestStepDescriptions:
    """Tests for STEP_DESCRIPTIONS constant."""

    def test_step_descriptions_not_empty(self):
        """Verify STEP_DESCRIPTIONS has entries."""
        assert len(STEP_DESCRIPTIONS) > 0

    def test_all_descriptions_are_strings(self):
        """Verify all step descriptions are non-empty strings."""
        for step, desc in STEP_DESCRIPTIONS.items():
            assert isinstance(desc, str), f"Description for {step} is not a string"
            assert len(desc) > 0, f"Description for {step} is empty"


class TestProseContextTemplates:
    """Tests for PROSE_CONTEXT_TEMPLATES mapping."""

    def test_all_steps_have_templates(self):
        """Verify all step descriptions have corresponding templates."""
        for step in STEP_DESCRIPTIONS:
            assert step in PROSE_CONTEXT_TEMPLATES, f"No template for step: {step}"

    def test_all_templates_are_callable(self):
        """Verify all templates are callable functions."""
        for step, template_fn in PROSE_CONTEXT_TEMPLATES.items():
            assert callable(template_fn), f"Template for {step} is not callable"

    def test_all_templates_accept_dict(self):
        """Verify all templates accept a dict argument and return string."""
        for step, template_fn in PROSE_CONTEXT_TEMPLATES.items():
            result = template_fn({})
            assert isinstance(result, str), f"Template for {step} did not return string"
            assert len(result) > 0, f"Template for {step} returned empty string"


class TestPeerReviewProseTemplates:
    """Tests for peer-review workflow prose templates."""

    def test_prose_commit(self):
        """Test commit step prose."""
        result = _prose_commit({})
        assert "commit" in result.lower()
        assert isinstance(result, str)

    def test_prose_create_pr_with_number(self):
        """Test create-pr step prose with PR number."""
        result = _prose_create_pr({"pr_number": "42"})
        assert "#42" in result
        assert "PR" in result

    def test_prose_create_pr_without_number(self):
        """Test create-pr step prose without PR number."""
        result = _prose_create_pr({})
        assert "pull request" in result.lower()

    def test_prose_invoke_reviewers_with_progress(self):
        """Test invoke-reviewers step prose with progress."""
        result = _prose_invoke_reviewers({"reviewers": "2/3", "blocking": "0"})
        assert "3" in result  # total reviewers
        assert "2" in result  # completed
        assert "blocking" in result.lower()

    def test_prose_invoke_reviewers_without_progress(self):
        """Test invoke-reviewers step prose without progress."""
        result = _prose_invoke_reviewers({})
        assert "reviewer" in result.lower()

    def test_prose_synthesize_no_blocking(self):
        """Test synthesize step prose with no blocking issues."""
        result = _prose_synthesize({"blocking": 0, "minor_count": "2", "future_count": "1"})
        assert "no blocking" in result.lower() or "2" in result

    def test_prose_synthesize_with_blocking(self):
        """Test synthesize step prose with blocking issues."""
        result = _prose_synthesize({"blocking": 2})
        assert "blocking" in result.lower()

    def test_prose_recommendations_no_blocking(self):
        """Test recommendations step prose with no blocking issues."""
        result = _prose_recommendations({"has_blocking": False, "minor_count": 3, "future_count": 1})
        assert "no blocking" in result.lower() or "3" in result

    def test_prose_recommendations_with_blocking(self):
        """Test recommendations step prose with blocking issues."""
        result = _prose_recommendations({"has_blocking": True})
        assert "blocking" in result.lower()

    def test_prose_merge_ready_no_blocking(self):
        """Test merge-ready step prose with no blocking issues."""
        result = _prose_merge_ready({"blocking": 0})
        assert "no blocking" in result.lower() or "ready for merge" in result.lower()

    def test_prose_merge_ready_with_blocking(self):
        """Test merge-ready step prose with blocking issues."""
        result = _prose_merge_ready({"blocking": 2})
        assert "blocking" in result.lower()

    def test_prose_awaiting_user_decision(self):
        """Test awaiting_user_decision step prose."""
        result = _prose_awaiting_user_decision({})
        assert "waiting" in result.lower() or "user" in result.lower()


class TestOrchestrateProseTemplates:
    """Tests for orchestrate workflow prose templates."""

    def test_prose_variety_assess(self):
        """Test variety-assess step prose."""
        result = _prose_variety_assess({})
        assert "complex" in result.lower() or "assess" in result.lower()

    def test_prose_prepare_with_feature(self):
        """Test prepare step prose with feature name."""
        result = _prose_prepare({"feature": "auth module"})
        assert "PREPARE" in result
        assert "auth module" in result

    def test_prose_prepare_without_feature(self):
        """Test prepare step prose without feature name."""
        result = _prose_prepare({})
        assert "PREPARE" in result

    def test_prose_architect(self):
        """Test architect step prose."""
        result = _prose_architect({})
        assert "ARCHITECT" in result

    def test_prose_code_with_phase(self):
        """Test code step prose with phase."""
        result = _prose_code({"phase": "backend"})
        assert "CODE" in result
        assert "backend" in result

    def test_prose_code_without_phase(self):
        """Test code step prose without phase."""
        result = _prose_code({})
        assert "CODE" in result

    def test_prose_test(self):
        """Test test step prose."""
        result = _prose_test({})
        assert "TEST" in result


class TestPlanModeProseTemplates:
    """Tests for plan-mode workflow prose templates."""

    def test_prose_analyze(self):
        """Test analyze step prose."""
        result = _prose_analyze({})
        assert "analyz" in result.lower() or "scope" in result.lower()

    def test_prose_consult(self):
        """Test consult step prose."""
        result = _prose_consult({})
        assert "consult" in result.lower() or "specialist" in result.lower()

    def test_prose_present_with_plan_file(self):
        """Test present step prose with plan file."""
        result = _prose_present({"plan_file": "auth-plan.md"})
        assert "plan" in result.lower()
        assert "auth-plan.md" in result

    def test_prose_present_without_plan_file(self):
        """Test present step prose without plan file."""
        result = _prose_present({})
        assert "plan" in result.lower()


class TestComPACTProseTemplates:
    """Tests for comPACT workflow prose templates."""

    def test_prose_invoking_specialist(self):
        """Test invoking-specialist step prose."""
        result = _prose_invoking_specialist({})
        assert "specialist" in result.lower() or "delegat" in result.lower()

    def test_prose_specialist_completed(self):
        """Test specialist-completed step prose."""
        result = _prose_specialist_completed({})
        assert "complet" in result.lower() or "specialist" in result.lower()


class TestRePACTProseTemplates:
    """Tests for rePACT (nested) workflow prose templates."""

    def test_prose_nested_prepare(self):
        """Test nested-prepare step prose."""
        result = _prose_nested_prepare({})
        assert "nested" in result.lower() and "PREPARE" in result

    def test_prose_nested_architect(self):
        """Test nested-architect step prose."""
        result = _prose_nested_architect({})
        assert "nested" in result.lower() and "ARCHITECT" in result

    def test_prose_nested_code(self):
        """Test nested-code step prose."""
        result = _prose_nested_code({})
        assert "nested" in result.lower() and "CODE" in result

    def test_prose_nested_test(self):
        """Test nested-test step prose."""
        result = _prose_nested_test({})
        assert "nested" in result.lower() and "TEST" in result


class TestImPACTProseTemplates:
    """Tests for imPACT (triage/blocker) workflow prose templates."""

    def test_prose_triage_with_blocker(self):
        """Test triage step prose with blocker description."""
        result = _prose_triage({"blocker": "missing API key"})
        assert "triage" in result.lower() or "blocker" in result.lower()
        assert "missing API key" in result

    def test_prose_triage_without_blocker(self):
        """Test triage step prose without blocker description."""
        result = _prose_triage({})
        assert "triage" in result.lower() or "blocker" in result.lower()

    def test_prose_assessing_redo_with_phase(self):
        """Test assessing-redo step prose with prior phase."""
        result = _prose_assessing_redo({"prior_phase": "PREPARE"})
        assert "redo" in result.lower()
        assert "PREPARE" in result

    def test_prose_assessing_redo_without_phase(self):
        """Test assessing-redo step prose without prior phase."""
        result = _prose_assessing_redo({})
        assert "redo" in result.lower()

    def test_prose_selecting_agents_with_list(self):
        """Test selecting-agents step prose with agent list."""
        result = _prose_selecting_agents({"agents": "backend, test"})
        assert "agent" in result.lower()
        assert "backend, test" in result

    def test_prose_selecting_agents_without_list(self):
        """Test selecting-agents step prose without agent list."""
        result = _prose_selecting_agents({})
        assert "agent" in result.lower()

    # --- v3.5.0 outcome names ---

    def test_prose_resolution_path_redo_prior_phase(self):
        """Test resolution-path with v3.5.0 redo_prior_phase outcome."""
        result = _prose_resolution_path({"outcome": "redo_prior_phase"})
        assert "redo prior phase" in result.lower()

    def test_prose_resolution_path_augment_present_phase(self):
        """Test resolution-path with v3.5.0 augment_present_phase outcome."""
        result = _prose_resolution_path({"outcome": "augment_present_phase"})
        assert "augment" in result.lower()

    def test_prose_resolution_path_invoke_repact(self):
        """Test resolution-path with v3.5.0 invoke_repact outcome."""
        result = _prose_resolution_path({"outcome": "invoke_repact"})
        assert "repact" in result.lower()

    def test_prose_resolution_path_terminate_agent(self):
        """Test resolution-path with v3.5.0 terminate_agent outcome."""
        result = _prose_resolution_path({"outcome": "terminate_agent"})
        assert "terminate" in result.lower()

    def test_prose_resolution_path_not_truly_blocked(self):
        """Test resolution-path with v3.5.0 not_truly_blocked outcome."""
        result = _prose_resolution_path({"outcome": "not_truly_blocked"})
        assert "not truly blocked" in result.lower()

    def test_prose_resolution_path_escalate_to_user(self):
        """Test resolution-path with v3.5.0 escalate_to_user outcome."""
        result = _prose_resolution_path({"outcome": "escalate_to_user"})
        assert "escalate" in result.lower()

    # --- v3.4 outcome names (backwards compat) ---

    def test_prose_resolution_path_redo_solo(self):
        """Test resolution-path step prose with redo_solo outcome."""
        result = _prose_resolution_path({"outcome": "redo_solo"})
        assert "redo" in result.lower()
        assert "solo" in result.lower()

    def test_prose_resolution_path_redo_with_help(self):
        """Test resolution-path step prose with redo_with_help outcome."""
        result = _prose_resolution_path({"outcome": "redo_with_help"})
        assert "redo" in result.lower()
        assert "help" in result.lower() or "assist" in result.lower()

    def test_prose_resolution_path_proceed_with_help(self):
        """Test resolution-path step prose with proceed_with_help outcome."""
        result = _prose_resolution_path({"outcome": "proceed_with_help"})
        assert "proceed" in result.lower()
        assert "help" in result.lower() or "assist" in result.lower()

    def test_prose_resolution_path_default(self):
        """Test resolution-path step prose with no specific outcome."""
        result = _prose_resolution_path({})
        assert "resolution" in result.lower() or "blocker" in result.lower()
