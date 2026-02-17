"""
Tests for the patterns module.

Tests workflow pattern compilation, termination signal detection,
context extraction, and regex pattern validation.
"""

import re
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from memory_prompt import PACT_AGENTS
from memory_enforce import PACT_WORK_AGENTS
from phase_completion import CODE_PHASE_INDICATORS
from shared.task_utils import find_active_agents  # agent_prefixes is local; we parse source

from refresh.patterns import (
    WORKFLOW_PATTERNS,
    TRIGGER_PATTERNS,
    STEP_MARKERS,
    TERMINATION_SIGNALS,
    CONTEXT_EXTRACTORS,
    PENDING_ACTION_PATTERNS,
    CONFIDENCE_WEIGHTS,
    CONFIDENCE_THRESHOLD,
    TERMINATION_WINDOW_TURNS,
    PENDING_ACTION_INSTRUCTION_MAX_LENGTH,
    REVIEW_PROMPT_INSTRUCTION_MAX_LENGTH,
    TASK_SUMMARY_MAX_LENGTH,
    PACT_AGENT_PATTERN,
    TASK_TOOL_PATTERN,
    SUBAGENT_TYPE_PATTERN,
    WorkflowPattern,
    compile_workflow_patterns,
    is_termination_signal,
    extract_context_value,
)


class TestWorkflowPatternDataclass:
    """Tests for the WorkflowPattern dataclass."""

    def test_workflow_pattern_fields(self):
        """Test WorkflowPattern has all required fields."""
        pattern = WorkflowPattern(
            name="test-workflow",
            trigger_pattern=re.compile(r"test"),
            step_markers=["step1", "step2"],
            termination_signals=["done"],
            context_extractors={},
        )

        assert pattern.name == "test-workflow"
        assert pattern.trigger_pattern.pattern == "test"
        assert pattern.step_markers == ["step1", "step2"]
        assert pattern.termination_signals == ["done"]
        assert pattern.context_extractors == {}


class TestCompileWorkflowPatterns:
    """Tests for compile_workflow_patterns function."""

    def test_returns_dict(self):
        """Test that compile_workflow_patterns returns a dict."""
        patterns = compile_workflow_patterns()
        assert isinstance(patterns, dict)

    def test_contains_expected_workflows(self):
        """Test that all expected workflow types are present."""
        patterns = compile_workflow_patterns()

        expected_workflows = [
            "peer-review",
            "orchestrate",
            "plan-mode",
            "comPACT",
            "rePACT",
            "imPACT",
        ]
        for workflow in expected_workflows:
            assert workflow in patterns, f"Missing workflow: {workflow}"

    def test_each_pattern_is_workflow_pattern(self):
        """Test each value is a WorkflowPattern instance."""
        patterns = compile_workflow_patterns()

        for name, pattern in patterns.items():
            assert isinstance(pattern, WorkflowPattern), f"{name} is not WorkflowPattern"

    def test_pattern_has_trigger(self):
        """Test each workflow has a compiled trigger pattern."""
        patterns = compile_workflow_patterns()

        for name, pattern in patterns.items():
            assert pattern.trigger_pattern is not None, f"{name} missing trigger"
            assert hasattr(pattern.trigger_pattern, "search"), f"{name} trigger not compiled"

    def test_pattern_has_step_markers(self):
        """Test each workflow has step markers."""
        patterns = compile_workflow_patterns()

        for name, pattern in patterns.items():
            assert isinstance(pattern.step_markers, list), f"{name} step_markers not list"
            # All workflows should have at least one step marker
            assert len(pattern.step_markers) > 0, f"{name} has no step markers"

    def test_pattern_has_termination_signals(self):
        """Test each workflow has termination signals."""
        patterns = compile_workflow_patterns()

        for name, pattern in patterns.items():
            assert isinstance(pattern.termination_signals, list)

    def test_pattern_has_context_extractors(self):
        """Test each workflow has context extractors (shared)."""
        patterns = compile_workflow_patterns()

        for name, pattern in patterns.items():
            assert pattern.context_extractors == CONTEXT_EXTRACTORS


class TestWorkflowPatternsConstant:
    """Tests for the pre-compiled WORKFLOW_PATTERNS constant."""

    def test_workflow_patterns_exists(self):
        """Test WORKFLOW_PATTERNS is populated."""
        assert WORKFLOW_PATTERNS is not None
        assert len(WORKFLOW_PATTERNS) > 0

    def test_workflow_patterns_contains_expected_keys(self):
        """Test WORKFLOW_PATTERNS has expected workflow types."""
        expected_keys = {"peer-review", "orchestrate", "plan-mode", "comPACT", "rePACT", "imPACT"}
        assert expected_keys.issubset(set(WORKFLOW_PATTERNS.keys()))

    def test_peer_review_pattern(self):
        """Test peer-review workflow pattern details."""
        pattern = WORKFLOW_PATTERNS["peer-review"]

        assert pattern.name == "peer-review"
        assert "commit" in pattern.step_markers
        assert "merge-ready" in pattern.step_markers
        assert any("merged" in sig for sig in pattern.termination_signals)

    def test_orchestrate_pattern(self):
        """Test orchestrate workflow pattern details."""
        pattern = WORKFLOW_PATTERNS["orchestrate"]

        assert pattern.name == "orchestrate"
        assert "prepare" in pattern.step_markers
        assert "architect" in pattern.step_markers
        assert "code" in pattern.step_markers
        assert "test" in pattern.step_markers

    def test_plan_mode_pattern(self):
        """Test plan-mode workflow pattern details."""
        pattern = WORKFLOW_PATTERNS["plan-mode"]

        assert pattern.name == "plan-mode"
        assert "analyze" in pattern.step_markers
        assert "consult" in pattern.step_markers
        assert "present" in pattern.step_markers

    def test_compact_pattern(self):
        """Test comPACT workflow pattern details."""
        pattern = WORKFLOW_PATTERNS["comPACT"]

        assert pattern.name == "comPACT"
        assert "invoking-specialist" in pattern.step_markers
        assert "specialist-completed" in pattern.step_markers

    def test_repact_pattern(self):
        """Test rePACT workflow pattern details."""
        pattern = WORKFLOW_PATTERNS["rePACT"]

        assert pattern.name == "rePACT"
        assert "nested-prepare" in pattern.step_markers
        assert "nested-code" in pattern.step_markers


class TestIsTerminationSignal:
    """Tests for is_termination_signal function."""

    def test_peer_review_merged(self):
        """Test detecting PR merged signal."""
        # Pattern: (?:PR|pull request)\s+(?:has been\s+)?merged
        # The pattern expects text like "PR has been merged" or "pull request merged"
        assert is_termination_signal("PR has been merged", "peer-review") is True
        assert is_termination_signal("The pull request has been merged successfully", "peer-review") is True
        assert is_termination_signal("PR merged to main", "peer-review") is True

    def test_peer_review_closed(self):
        """Test detecting PR closed signal."""
        assert is_termination_signal("PR closed without merging", "peer-review") is True

    def test_peer_review_declined(self):
        """Test detecting user declined signal."""
        assert is_termination_signal("User declined the merge", "peer-review") is True

    def test_peer_review_merge_complete(self):
        """Test detecting merge complete signal."""
        assert is_termination_signal("Merge complete. All done!", "peer-review") is True

    def test_peer_review_successfully_merged(self):
        """Test detecting successfully merged signal."""
        assert is_termination_signal("PR successfully merged to main", "peer-review") is True

    def test_peer_review_no_termination(self):
        """Test non-termination content."""
        assert is_termination_signal("Still working on the review...", "peer-review") is False
        assert is_termination_signal("Invoking reviewers now.", "peer-review") is False

    def test_orchestrate_all_phases_complete(self):
        """Test detecting orchestrate completion."""
        assert is_termination_signal("All phases complete. Task done.", "orchestrate") is True
        assert is_termination_signal("All phase complete now", "orchestrate") is True

    def test_orchestrate_implemented(self):
        """Test detecting IMPLEMENTED signal."""
        assert is_termination_signal("IMPLEMENTED: Auth endpoint ready", "orchestrate") is True

    def test_orchestrate_workflow_complete(self):
        """Test detecting workflow complete signal."""
        assert is_termination_signal("Workflow complete. Ready for review.", "orchestrate") is True

    def test_orchestrate_orchestration_complete(self):
        """Test detecting orchestration complete signal."""
        assert is_termination_signal("Orchestration complete. All agents finished.", "orchestrate") is True

    def test_orchestrate_no_termination(self):
        """Test non-termination content for orchestrate."""
        assert is_termination_signal("Starting code phase...", "orchestrate") is False

    def test_plan_mode_plan_saved(self):
        """Test detecting plan saved signal."""
        assert is_termination_signal("Plan saved to docs/plans/feature.md", "plan-mode") is True

    def test_plan_mode_plan_presented(self):
        """Test detecting plan presented signal."""
        assert is_termination_signal("Plan presented to user for review.", "plan-mode") is True

    def test_plan_mode_awaiting_approval(self):
        """Test detecting awaiting approval signal."""
        assert is_termination_signal("Awaiting approval to proceed.", "plan-mode") is True

    def test_plan_mode_plan_complete(self):
        """Test detecting plan complete signal."""
        assert is_termination_signal("Plan complete. Ready for implementation.", "plan-mode") is True

    def test_compact_specialist_completed(self):
        """Test detecting comPACT completion."""
        assert is_termination_signal("Specialist completed the task.", "comPACT") is True

    def test_compact_task_complete(self):
        """Test detecting task complete signal."""
        assert is_termination_signal("Task complete. Bug fixed.", "comPACT") is True

    def test_compact_handoff_complete(self):
        """Test detecting handoff complete signal."""
        assert is_termination_signal("Handoff complete. Results delivered.", "comPACT") is True

    def test_repact_nested_cycle_complete(self):
        """Test detecting rePACT completion."""
        assert is_termination_signal("Nested cycle complete. Returning to parent.", "rePACT") is True

    def test_repact_repact_complete(self):
        """Test detecting rePACT complete signal."""
        assert is_termination_signal("rePACT complete. Sub-module ready.", "rePACT") is True

    def test_unknown_workflow_returns_false(self):
        """Test unknown workflow always returns False."""
        assert is_termination_signal("anything", "unknown-workflow") is False

    def test_empty_content(self):
        """Test empty content returns False."""
        assert is_termination_signal("", "peer-review") is False

    def test_case_insensitive(self):
        """Test termination signals are case insensitive."""
        assert is_termination_signal("PR HAS BEEN MERGED", "peer-review") is True
        assert is_termination_signal("all phases complete", "orchestrate") is True


class TestExtractContextValue:
    """Tests for extract_context_value function."""

    def test_extract_pr_number(self):
        """Test extracting PR number."""
        result = extract_context_value("Created PR #64 for review", "pr_number")
        assert result == "64"

    def test_extract_pr_number_with_pull_request(self):
        """Test extracting PR number with 'pull request' text."""
        result = extract_context_value("Pull request 123 is ready", "pr_number")
        assert result == "123"

    def test_extract_pr_number_no_hash(self):
        """Test extracting PR number without hash symbol."""
        result = extract_context_value("PR 42 created", "pr_number")
        assert result == "42"

    def test_extract_branch_name(self):
        """Test extracting branch name."""
        result = extract_context_value("Working on branch: feature/auth-login", "branch_name")
        assert result == "feature/auth-login"

    def test_extract_branch_name_feature_prefix(self):
        """Test extracting branch with feature prefix."""
        result = extract_context_value("feature: add-new-endpoint", "branch_name")
        assert result == "add-new-endpoint"

    def test_extract_task_summary(self):
        """Test extracting task summary."""
        result = extract_context_value("task: implementing the user authentication module", "task_summary")
        assert result is not None
        assert "user authentication" in result

    def test_extract_task_summary_implementing(self):
        """Test extracting task summary with 'implementing' keyword."""
        result = extract_context_value("Implementing the new caching layer for performance", "task_summary")
        assert result is not None

    def test_extract_task_summary_working_on(self):
        """Test extracting task summary with 'working on' keyword."""
        result = extract_context_value("Working on: fixing the database connection issue", "task_summary")
        assert result is not None

    def test_extract_unknown_key_returns_none(self):
        """Test unknown context key returns None."""
        result = extract_context_value("any content", "unknown_key")
        assert result is None

    def test_extract_no_match_returns_none(self):
        """Test no pattern match returns None."""
        result = extract_context_value("no relevant content here", "pr_number")
        assert result is None

    def test_extract_strips_whitespace(self):
        """Test extracted values have whitespace stripped."""
        # Pattern: (?:PR|pull request)\s*#?(\d+)
        # Note: The pattern captures just digits, extra spaces between # and number won't match
        result = extract_context_value("PR #99 is ready  ", "pr_number")
        assert result == "99"


class TestRegexPatternEdgeCases:
    """Tests for regex pattern edge cases including special characters and unicode."""

    def test_trigger_pattern_with_special_characters(self):
        """Test trigger patterns handle special characters in surrounding text."""
        pattern = TRIGGER_PATTERNS["peer-review"]

        # Should match with special characters nearby
        assert pattern.search("/PACT:peer-review!") is not None
        assert pattern.search("Run: /PACT:peer-review now") is not None
        assert pattern.search(">>> /PACT:peer-review <<<") is not None

    def test_trigger_pattern_case_insensitive(self):
        """Test trigger patterns are case insensitive."""
        pattern = TRIGGER_PATTERNS["peer-review"]

        assert pattern.search("/PACT:PEER-REVIEW") is not None
        assert pattern.search("/pact:peer-review") is not None
        assert pattern.search("/Pact:Peer-Review") is not None

    def test_trigger_pattern_unicode_surrounding_text(self):
        """Test trigger patterns work with unicode surrounding text."""
        pattern = TRIGGER_PATTERNS["orchestrate"]

        assert pattern.search("/PACT:orchestrate implement feature") is not None
        assert pattern.search("/PACT:orchestrate  test") is not None

    def test_context_extractor_pr_edge_cases(self):
        """Test PR number extraction edge cases."""
        pattern = CONTEXT_EXTRACTORS["pr_number"]

        # Various formats
        assert pattern.search("PR#123").group(1) == "123"
        assert pattern.search("PR #456").group(1) == "456"
        assert pattern.search("Pull Request #789").group(1) == "789"
        assert pattern.search("pull request 101").group(1) == "101"

        # Should not match
        assert pattern.search("The problem is #123") is None

    def test_context_extractor_branch_edge_cases(self):
        """Test branch name extraction edge cases."""
        pattern = CONTEXT_EXTRACTORS["branch_name"]

        # Various branch name formats
        match = pattern.search("branch: feat/add-auth")
        assert match is not None

        match = pattern.search("feature: fix_bug_123")
        assert match is not None

    def test_pending_action_pattern_multiline(self):
        """Test AskUserQuestion pattern with multiline content."""
        pattern = PENDING_ACTION_PATTERNS["AskUserQuestion"]

        content = "AskUserQuestion: Would you like to\ncontinue with the merge?"
        match = pattern.search(content)
        assert match is not None

    def test_pending_action_awaiting_input_variations(self):
        """Test awaiting input pattern variations."""
        pattern = PENDING_ACTION_PATTERNS["awaiting_input"]

        assert pattern.search("Waiting for user input") is not None
        assert pattern.search("Awaiting response from user") is not None
        assert pattern.search("Need input to continue") is not None
        assert pattern.search("waiting for approval") is not None

    def test_review_prompt_variations(self):
        """Test review prompt pattern variations."""
        # Pattern: (?:would you like to|do you want to|shall I)\s+(.{10,150})
        # Requires 10-150 characters after the prompt phrase
        pattern = PENDING_ACTION_PATTERNS["review_prompt"]

        assert pattern.search("Would you like to continue with this task?") is not None
        assert pattern.search("Do you want to proceed with the merge?") is not None
        assert pattern.search("Shall I create the PR now and push it?") is not None

    def test_pact_agent_pattern(self):
        """Test PACT agent pattern matching with exact full agent names."""
        assert PACT_AGENT_PATTERN.search("pact-preparer") is not None
        assert PACT_AGENT_PATTERN.search("pact-architect") is not None
        assert PACT_AGENT_PATTERN.search("pact-backend-coder") is not None
        assert PACT_AGENT_PATTERN.search("pact-frontend-coder") is not None
        assert PACT_AGENT_PATTERN.search("pact-database-engineer") is not None
        assert PACT_AGENT_PATTERN.search("pact-devops-engineer") is not None
        assert PACT_AGENT_PATTERN.search("pact-n8n") is not None
        assert PACT_AGENT_PATTERN.search("pact-security-engineer") is not None
        assert PACT_AGENT_PATTERN.search("pact-qa-engineer") is not None
        assert PACT_AGENT_PATTERN.search("pact-test-engineer") is not None
        assert PACT_AGENT_PATTERN.search("pact-memory-agent") is not None

        # Should not match non-agent strings
        assert PACT_AGENT_PATTERN.search("other-agent") is None
        # Should not match partial stems (tightened regex)
        assert PACT_AGENT_PATTERN.search("pact-testing-strategies") is None
        assert PACT_AGENT_PATTERN.search("pact-frontend-design") is None

    def test_task_tool_pattern(self):
        """Test Task tool pattern matching."""
        assert TASK_TOOL_PATTERN.search('"name": "Task"') is not None
        assert TASK_TOOL_PATTERN.search('"name":"Task"') is not None
        assert TASK_TOOL_PATTERN.search('"name": "task"') is not None  # Case insensitive

    def test_subagent_type_pattern(self):
        """Test subagent type extraction pattern."""
        match = SUBAGENT_TYPE_PATTERN.search('"subagent_type": "pact-backend-coder"')
        assert match is not None
        assert match.group(1) == "pact-backend-coder"


class TestConstants:
    """Tests for constant values."""

    def test_confidence_threshold(self):
        """Test confidence threshold is reasonable."""
        assert 0 < CONFIDENCE_THRESHOLD < 1
        assert CONFIDENCE_THRESHOLD == 0.3

    def test_termination_window_turns(self):
        """Test termination window is reasonable."""
        assert TERMINATION_WINDOW_TURNS > 0
        assert TERMINATION_WINDOW_TURNS == 10

    def test_length_caps(self):
        """Test length cap constants are reasonable."""
        assert PENDING_ACTION_INSTRUCTION_MAX_LENGTH > 0
        assert REVIEW_PROMPT_INSTRUCTION_MAX_LENGTH > 0
        assert TASK_SUMMARY_MAX_LENGTH > 0

        assert PENDING_ACTION_INSTRUCTION_MAX_LENGTH == 200
        assert REVIEW_PROMPT_INSTRUCTION_MAX_LENGTH == 150
        assert TASK_SUMMARY_MAX_LENGTH == 200

    def test_confidence_weights_sum(self):
        """Test confidence weights sum to 1.0."""
        total = sum(CONFIDENCE_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001  # Allow small floating point error

    def test_confidence_weights_keys(self):
        """Test confidence weights have expected keys."""
        expected_keys = {
            "clear_trigger",
            "step_marker",
            "agent_invocation",
            "pending_action",
            "context_richness",
        }
        assert set(CONFIDENCE_WEIGHTS.keys()) == expected_keys

    def test_trigger_patterns_keys(self):
        """Test trigger patterns have expected workflow keys."""
        expected_keys = {"peer-review", "orchestrate", "plan-mode", "comPACT", "rePACT", "imPACT"}
        assert set(TRIGGER_PATTERNS.keys()) == expected_keys

    def test_step_markers_keys(self):
        """Test step markers have expected workflow keys."""
        expected_keys = {"peer-review", "orchestrate", "plan-mode", "comPACT", "rePACT", "imPACT"}
        assert set(STEP_MARKERS.keys()) == expected_keys

    def test_termination_signals_keys(self):
        """Test termination signals have expected workflow keys."""
        expected_keys = {"peer-review", "orchestrate", "plan-mode", "comPACT", "rePACT", "imPACT"}
        assert set(TERMINATION_SIGNALS.keys()) == expected_keys


class TestAgentListConsistency:
    """Cross-list consistency tests for hardcoded agent lists across hook modules.

    Validates that PACT_AGENTS (memory_prompt), PACT_WORK_AGENTS (memory_enforce),
    agent_prefixes (task_utils), and PACT_AGENT_PATTERN (patterns) stay in sync.
    """

    def test_work_agents_subset_of_pact_agents(self):
        """Every agent in PACT_WORK_AGENTS should also be in PACT_AGENTS."""
        for agent in PACT_WORK_AGENTS:
            assert agent in PACT_AGENTS, (
                f"{agent} is in PACT_WORK_AGENTS but missing from PACT_AGENTS"
            )

    def test_pact_agents_minus_memory_equals_work_agents(self):
        """PACT_AGENTS minus pact-memory-agent should equal PACT_WORK_AGENTS."""
        expected = [a for a in PACT_AGENTS if a != "pact-memory-agent"]
        assert expected == PACT_WORK_AGENTS, (
            f"PACT_WORK_AGENTS should be PACT_AGENTS minus pact-memory-agent.\n"
            f"Expected: {expected}\n"
            f"Got: {PACT_WORK_AGENTS}"
        )

    def test_pact_agent_pattern_matches_all_agents(self):
        """PACT_AGENT_PATTERN regex should match every agent in PACT_AGENTS."""
        for agent in PACT_AGENTS:
            assert PACT_AGENT_PATTERN.search(agent) is not None, (
                f"PACT_AGENT_PATTERN does not match '{agent}'"
            )

    def test_pact_agents_ordering_matches_lifecycle(self):
        """Agent lists should follow the lifecycle ordering convention."""
        # Canonical lifecycle order (from CLAUDE.md roster)
        lifecycle_order = [
            "pact-preparer",
            "pact-architect",
            "pact-backend-coder",
            "pact-frontend-coder",
            "pact-database-engineer",
            "pact-devops-engineer",
            "pact-n8n",
            "pact-test-engineer",
            "pact-security-engineer",
            "pact-qa-engineer",
            "pact-memory-agent",
        ]
        assert PACT_AGENTS == lifecycle_order, (
            f"PACT_AGENTS not in lifecycle order.\n"
            f"Expected: {lifecycle_order}\n"
            f"Got: {PACT_AGENTS}"
        )

    def test_work_agents_ordering_matches_lifecycle(self):
        """PACT_WORK_AGENTS should follow lifecycle ordering (minus memory-agent)."""
        lifecycle_order_no_memory = [
            "pact-preparer",
            "pact-architect",
            "pact-backend-coder",
            "pact-frontend-coder",
            "pact-database-engineer",
            "pact-devops-engineer",
            "pact-n8n",
            "pact-test-engineer",
            "pact-security-engineer",
            "pact-qa-engineer",
        ]
        assert PACT_WORK_AGENTS == lifecycle_order_no_memory, (
            f"PACT_WORK_AGENTS not in lifecycle order.\n"
            f"Expected: {lifecycle_order_no_memory}\n"
            f"Got: {PACT_WORK_AGENTS}"
        )

    @staticmethod
    def _parse_prefixes_from_source(filepath: str, marker: str) -> list[str]:
        """Parse agent prefix strings from a Python source file.

        Finds the marker string and extracts all quoted 'pact-*:' entries
        from the surrounding block (up to 600 chars after the marker).
        """
        source = Path(filepath).read_text()
        idx = source.index(marker)
        # Grab a generous window after the marker to capture the full literal
        block = source[idx:idx + 600]
        return re.findall(r'"(pact-[^"]+:)"', block)

    def test_stop_audit_prefixes_match_pact_agents(self):
        """stop_audit.py inline prefixes should match PACT_AGENTS (with colon suffix)."""
        stop_audit_path = str(
            Path(__file__).parent.parent / "hooks" / "stop_audit.py"
        )
        prefixes = self._parse_prefixes_from_source(
            stop_audit_path, "subject.lower().startswith"
        )
        expected = [a + ":" for a in PACT_AGENTS]
        assert prefixes == expected, (
            f"stop_audit.py agent prefixes out of sync with PACT_AGENTS.\n"
            f"Expected: {expected}\n"
            f"Got: {prefixes}"
        )

    def test_task_utils_prefixes_match_pact_agents(self):
        """task_utils.py agent_prefixes should match PACT_AGENTS (with colon suffix)."""
        task_utils_path = str(
            Path(__file__).parent.parent / "hooks" / "shared" / "task_utils.py"
        )
        prefixes = self._parse_prefixes_from_source(
            task_utils_path, "agent_prefixes = ("
        )
        expected = [a + ":" for a in PACT_AGENTS]
        assert prefixes == expected, (
            f"task_utils.py agent_prefixes out of sync with PACT_AGENTS.\n"
            f"Expected: {expected}\n"
            f"Got: {prefixes}"
        )

    def test_code_phase_indicators_are_valid_subset(self):
        """CODE_PHASE_INDICATORS should only contain known agent name stems."""
        # Extract the base agent name from each indicator
        # Indicators use both hyphen (pact-backend-coder) and underscore (pact_backend_coder) forms
        known_stems = {a.replace("pact-", "") for a in PACT_AGENTS}

        for indicator in CODE_PHASE_INDICATORS:
            # Normalize: strip "pact-" or "pact_" prefix, convert underscores to hyphens
            normalized = indicator.replace("pact_", "").replace("pact-", "")
            normalized = normalized.replace("_", "-")
            assert normalized in known_stems, (
                f"CODE_PHASE_INDICATORS entry '{indicator}' does not correspond "
                f"to a known PACT agent. Known stems: {sorted(known_stems)}"
            )
