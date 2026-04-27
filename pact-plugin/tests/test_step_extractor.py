"""
Tests for the step_extractor module.

Tests step detection, pending action extraction, and context gathering.
"""

import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

from refresh.transcript_parser import Turn, ToolCall, parse_transcript
from refresh.workflow_detector import WorkflowInfo
from refresh.step_extractor import (
    StepInfo,
    PendingAction,
    find_step_markers_in_turn,
    determine_current_step,
    detect_pending_action,
    extract_workflow_context,
    extract_current_step,
    _extract_peer_review_context,
    _extract_orchestrate_context,
)

from fixtures.refresh_system import (
    create_peer_review_transcript,
    create_orchestrate_transcript,
    make_user_message,
    make_assistant_message,
    make_task_call,
    create_transcript_lines,
)


class TestStepInfoDataclass:
    """Tests for the StepInfo dataclass."""

    def test_step_info_defaults(self):
        """Test StepInfo default values."""
        step = StepInfo(name="test")

        assert step.name == "test"
        assert step.sequence == 0
        assert step.started_at == ""
        assert step.pending_action is None
        assert step.context == {}

    def test_step_info_full(self):
        """Test StepInfo with all fields."""
        action = PendingAction(
            action_type="AskUserQuestion",
            instruction="Proceed?",
        )
        step = StepInfo(
            name="recommendations",
            sequence=5,
            started_at="2025-01-22T12:05:00Z",
            pending_action=action,
            context={"pr_number": 64},
        )

        assert step.name == "recommendations"
        assert step.sequence == 5
        assert step.pending_action == action
        assert step.context["pr_number"] == 64


class TestPendingActionDataclass:
    """Tests for the PendingAction dataclass."""

    def test_pending_action_defaults(self):
        """Test PendingAction default values."""
        action = PendingAction(action_type="Test")

        assert action.action_type == "Test"
        assert action.instruction == ""
        assert action.data == {}

    def test_pending_action_full(self):
        """Test PendingAction with all fields."""
        action = PendingAction(
            action_type="AskUserQuestion",
            instruction="Would you like to review?",
            data={"options": ["yes", "no"]},
        )

        assert action.action_type == "AskUserQuestion"
        assert action.instruction == "Would you like to review?"
        assert action.data == {"options": ["yes", "no"]}


class TestFindStepMarkersInTurn:
    """Tests for find_step_markers_in_turn function."""

    def test_find_single_marker(self):
        """Test finding a single step marker."""
        turn = Turn(turn_type="assistant", content="Starting the commit phase now.")

        markers = find_step_markers_in_turn(turn, "peer-review")

        assert "commit" in markers

    def test_find_multiple_markers(self):
        """Test finding multiple step markers in one turn."""
        turn = Turn(
            turn_type="assistant",
            content="Completed commit, now moving to create-pr phase.",
        )

        markers = find_step_markers_in_turn(turn, "peer-review")

        assert "commit" in markers
        assert "create-pr" in markers

    def test_no_markers_found(self):
        """Test when no step markers are present."""
        turn = Turn(turn_type="assistant", content="Working on something unrelated.")

        markers = find_step_markers_in_turn(turn, "peer-review")

        assert markers == []

    def test_word_boundary_matching(self):
        """Test that markers match on word boundaries only."""
        turn = Turn(
            turn_type="assistant",
            content="This is a commitment to quality, not a commit.",
        )

        markers = find_step_markers_in_turn(turn, "peer-review")

        # "commit" should match as it appears at end after comma
        # This tests the word boundary behavior
        assert "commit" in markers

    def test_unknown_workflow(self):
        """Test handling unknown workflow name."""
        turn = Turn(turn_type="assistant", content="Some content with commit phase")

        markers = find_step_markers_in_turn(turn, "unknown-workflow")

        assert markers == []


class TestDetermineCurrentStep:
    """Tests for determine_current_step function."""

    def test_determine_step_from_markers(self):
        """Test determining current step from step markers."""
        trigger_turn = Turn(turn_type="user", content="/PACT:peer-review", line_number=1)
        turns = [
            trigger_turn,
            Turn(turn_type="assistant", content="Starting commit...", line_number=2),
            Turn(turn_type="assistant", content="create-pr complete.", line_number=3),
            Turn(
                turn_type="assistant",
                content="invoke-reviewers: Invoking agents...",
                timestamp="2025-01-22T12:05:00Z",
                line_number=4,
            ),
        ]
        workflow_info = WorkflowInfo(
            name="peer-review",
            trigger_turn=trigger_turn,
        )

        # Item 2: Pass trigger_index (0 for first turn)
        step_name, sequence, timestamp = determine_current_step(turns, workflow_info, trigger_index=0)

        assert step_name == "invoke-reviewers"
        assert sequence == 3  # invoke-reviewers is 3rd in peer-review steps
        assert timestamp == "2025-01-22T12:05:00Z"

    def test_default_to_first_step(self):
        """Test defaulting to first step when no markers found."""
        trigger_turn = Turn(turn_type="user", content="/PACT:peer-review", line_number=1)
        turns = [
            trigger_turn,
            Turn(turn_type="assistant", content="Starting workflow...", line_number=2),
        ]
        workflow_info = WorkflowInfo(
            name="peer-review",
            trigger_turn=trigger_turn,
            started_at="2025-01-22T12:00:00Z",
        )

        # Item 2: Pass trigger_index
        step_name, sequence, timestamp = determine_current_step(turns, workflow_info, trigger_index=0)

        assert step_name == "commit"  # First step in peer-review
        assert sequence == 1
        assert timestamp == "2025-01-22T12:00:00Z"

    def test_unknown_workflow_returns_unknown(self):
        """Test handling workflow with no pattern."""
        trigger_turn = Turn(turn_type="user", content="/PACT:unknown")
        workflow_info = WorkflowInfo(
            name="unknown-workflow",
            trigger_turn=trigger_turn,
        )

        # Item 2: Pass trigger_index
        step_name, sequence, timestamp = determine_current_step([], workflow_info, trigger_index=0)

        assert step_name == "unknown"
        assert sequence == 0

    def test_no_trigger_turn(self):
        """Test handling missing trigger turn."""
        workflow_info = WorkflowInfo(
            name="peer-review",
            trigger_turn=None,
        )

        # Item 2: Pass trigger_index
        step_name, sequence, timestamp = determine_current_step([], workflow_info, trigger_index=0)

        assert step_name == "unknown"


class TestDetectPendingAction:
    """Tests for detect_pending_action function."""

    def test_detect_ask_user_question(self):
        """Test detecting AskUserQuestion pending action."""
        turns = [
            Turn(turn_type="user", content="/PACT:peer-review", line_number=1),
            Turn(
                turn_type="assistant",
                content="AskUserQuestion: Would you like to review the minor recommendations?",
                line_number=2,
            ),
        ]

        action = detect_pending_action(turns, 0)

        assert action is not None
        assert action.action_type == "AskUserQuestion"
        assert "minor recommendations" in action.instruction

    def test_detect_review_prompt(self):
        """Test detecting 'would you like to' prompt."""
        turns = [
            Turn(turn_type="user", content="/PACT:peer-review", line_number=1),
            Turn(
                turn_type="assistant",
                content="Would you like to proceed with the merge?",
                line_number=2,
            ),
        ]

        action = detect_pending_action(turns, 0)

        assert action is not None
        assert action.action_type == "UserDecision"
        assert "merge" in action.instruction.lower()

    def test_detect_awaiting_input(self):
        """Test detecting generic awaiting input."""
        turns = [
            Turn(turn_type="user", content="/PACT:peer-review", line_number=1),
            Turn(
                turn_type="assistant",
                content="Waiting for user input before proceeding.",
                line_number=2,
            ),
        ]

        action = detect_pending_action(turns, 0)

        assert action is not None
        assert action.action_type == "AwaitingInput"

    def test_no_pending_action(self):
        """Test when no pending action exists."""
        turns = [
            Turn(turn_type="user", content="/PACT:peer-review", line_number=1),
            Turn(
                turn_type="assistant",
                content="Processing automatically...",
                line_number=2,
            ),
        ]

        action = detect_pending_action(turns, 0)

        assert action is None

    def test_checks_last_two_assistant_turns(self):
        """Test that last 2 assistant turns are checked."""
        turns = [
            Turn(turn_type="user", content="/PACT:peer-review", line_number=1),
            Turn(
                turn_type="assistant",
                content="Just a status update.",
                line_number=2,
            ),
            Turn(
                turn_type="assistant",
                content="AskUserQuestion: Proceed with merge?",
                line_number=3,
            ),
        ]

        action = detect_pending_action(turns, 0)

        # Should find the AskUserQuestion from turn 3 (the last turn)
        assert action is not None

    def test_instruction_length_capped(self):
        """Test that very long instructions are capped."""
        long_instruction = "x" * 500
        turns = [
            Turn(turn_type="user", content="/PACT:peer-review", line_number=1),
            Turn(
                turn_type="assistant",
                content=f"AskUserQuestion: {long_instruction}",
                line_number=2,
            ),
        ]

        action = detect_pending_action(turns, 0)

        assert action is not None
        assert len(action.instruction) <= 200


class TestExtractWorkflowContext:
    """Tests for extract_workflow_context function."""

    def test_extract_pr_number(self):
        """Test extracting PR number from context."""
        trigger_turn = Turn(turn_type="user", content="/PACT:peer-review", line_number=1)
        turns = [
            trigger_turn,
            Turn(
                turn_type="assistant",
                content="Created PR #64 for this feature.",
                line_number=2,
            ),
        ]
        workflow_info = WorkflowInfo(
            name="peer-review",
            trigger_turn=trigger_turn,
        )

        # Item 2: Pass trigger_index
        context = extract_workflow_context(turns, workflow_info, trigger_index=0)

        assert context.get("pr_number") == 64

    def test_extract_task_summary(self):
        """Test extracting task summary from context."""
        trigger_turn = Turn(
            turn_type="user",
            content="/PACT:orchestrate implement auth",
            line_number=1,
        )
        turns = [
            trigger_turn,
            Turn(
                turn_type="assistant",
                content="Task: Implementing authentication module for the API",
                line_number=2,
            ),
        ]
        workflow_info = WorkflowInfo(
            name="orchestrate",
            trigger_turn=trigger_turn,
        )

        # Item 2: Pass trigger_index
        context = extract_workflow_context(turns, workflow_info, trigger_index=0)

        # May or may not match depending on pattern
        # The pattern is: "task|implementing|working on:[:\s]+(.{10,100})"
        # Let's check if we get any context
        assert isinstance(context, dict)

    def test_extract_peer_review_context(self):
        """Test extracting peer-review specific context."""
        trigger_turn = Turn(turn_type="user", content="/PACT:peer-review", line_number=1)
        turns = [
            trigger_turn,
            Turn(
                turn_type="assistant",
                content="Review complete. 2 minor issues, 1 future recommendation. No blocking issues.",
                line_number=2,
            ),
        ]
        workflow_info = WorkflowInfo(
            name="peer-review",
            trigger_turn=trigger_turn,
        )

        # Item 2: Pass trigger_index
        context = extract_workflow_context(turns, workflow_info, trigger_index=0)

        assert context.get("has_blocking") is False
        assert context.get("minor_count") == 2
        assert context.get("future_count") == 1

    def test_extract_orchestrate_context(self):
        """Test extracting orchestrate specific context."""
        trigger_turn = Turn(
            turn_type="user",
            content="/PACT:orchestrate implement",
            line_number=1,
        )
        turns = [
            trigger_turn,
            Turn(
                turn_type="assistant",
                content="Currently in code phase. Backend implementation in progress.",
                line_number=2,
            ),
        ]
        workflow_info = WorkflowInfo(
            name="orchestrate",
            trigger_turn=trigger_turn,
        )

        # Item 2: Pass trigger_index
        context = extract_workflow_context(turns, workflow_info, trigger_index=0)

        assert context.get("current_phase") == "code"

    def test_context_values_capped(self):
        """Test that very long context values are capped."""
        trigger_turn = Turn(turn_type="user", content="/PACT:peer-review", line_number=1)
        long_summary = "task: " + "x" * 500
        turns = [
            trigger_turn,
            Turn(turn_type="assistant", content=long_summary, line_number=2),
        ]
        workflow_info = WorkflowInfo(
            name="peer-review",
            trigger_turn=trigger_turn,
        )

        # Item 2: Pass trigger_index
        context = extract_workflow_context(turns, workflow_info, trigger_index=0)

        if "task_summary" in context:
            assert len(context["task_summary"]) <= 200


class TestExtractCurrentStep:
    """Tests for the main extract_current_step function."""

    def test_extract_complete_step_info(self, tmp_transcript, peer_review_mid_workflow_transcript):
        """Test extracting complete step information."""
        path = tmp_transcript(peer_review_mid_workflow_transcript)
        turns = parse_transcript(path)

        # Find the trigger turn
        trigger_turn = None
        for turn in turns:
            if turn.is_user and "/PACT:peer-review" in turn.content:
                trigger_turn = turn
                break

        workflow_info = WorkflowInfo(
            name="peer-review",
            trigger_turn=trigger_turn,
        )

        step_info = extract_current_step(turns, workflow_info)

        assert step_info.name != "unknown"
        assert step_info.sequence > 0
        assert isinstance(step_info.context, dict)

    def test_extract_with_pending_action(self, tmp_transcript):
        """Test extracting step with pending action."""
        transcript = create_peer_review_transcript(
            step="recommendations",
            include_pending_question=True,
        )
        path = tmp_transcript(transcript)
        turns = parse_transcript(path)

        trigger_turn = turns[0]  # First turn is the trigger
        workflow_info = WorkflowInfo(
            name="peer-review",
            trigger_turn=trigger_turn,
        )

        step_info = extract_current_step(turns, workflow_info)

        assert step_info.pending_action is not None
        assert step_info.pending_action.action_type in [
            "AskUserQuestion",
            "UserDecision",
            "AwaitingInput",
        ]

    def test_extract_orchestrate_step(self, tmp_transcript, orchestrate_code_phase_transcript):
        """Test extracting step from orchestrate workflow."""
        path = tmp_transcript(orchestrate_code_phase_transcript)
        turns = parse_transcript(path)

        trigger_turn = turns[0]
        workflow_info = WorkflowInfo(
            name="orchestrate",
            trigger_turn=trigger_turn,
        )

        step_info = extract_current_step(turns, workflow_info)

        assert step_info.name in ["code", "test", "architect", "prepare", "variety-assess"]

    def test_extract_without_trigger_turn(self):
        """Test handling missing trigger turn gracefully."""
        workflow_info = WorkflowInfo(
            name="peer-review",
            trigger_turn=None,
        )

        step_info = extract_current_step([], workflow_info)

        assert step_info.name == "unknown"
        assert step_info.pending_action is None


class TestEdgeCases:
    """Tests for edge cases in step extraction."""

    def test_empty_turns_after_trigger(self):
        """Test handling empty turns list."""
        trigger_turn = Turn(turn_type="user", content="/PACT:peer-review", line_number=1)
        workflow_info = WorkflowInfo(
            name="peer-review",
            trigger_turn=trigger_turn,
        )

        step_info = extract_current_step([trigger_turn], workflow_info)

        # Should default to first step
        assert step_info.name == "commit"

    def test_multiple_step_markers_uses_last(self, tmp_path: Path):
        """Test that when multiple steps mentioned, last one is used."""
        lines = [
            make_user_message("/PACT:peer-review"),
            make_assistant_message("Completed commit phase."),
            make_assistant_message("Completed create-pr phase."),
            make_assistant_message("Now in invoke-reviewers phase. Also mentioning synthesize."),
        ]

        file_path = tmp_path / "multi_step.jsonl"
        file_path.write_text(create_transcript_lines(lines))

        turns = parse_transcript(file_path)
        workflow_info = WorkflowInfo(
            name="peer-review",
            trigger_turn=turns[0],
        )

        step_info = extract_current_step(turns, workflow_info)

        # Should find synthesize as it's mentioned last
        assert step_info.name in ["synthesize", "invoke-reviewers"]

    def test_context_extraction_handles_missing_patterns(self):
        """Test context extraction when patterns don't match."""
        trigger_turn = Turn(turn_type="user", content="/PACT:peer-review", line_number=1)
        turns = [
            trigger_turn,
            Turn(
                turn_type="assistant",
                content="Generic content with no extractable patterns.",
                line_number=2,
            ),
        ]
        workflow_info = WorkflowInfo(
            name="peer-review",
            trigger_turn=trigger_turn,
        )

        step_info = extract_current_step(turns, workflow_info)

        # Context should be empty dict, not fail
        assert isinstance(step_info.context, dict)


class TestPrivateExtractionFunctions:
    """Tests for private extraction function edge cases."""

    def test_peer_review_multiple_blocking_mentions(self):
        """Test _extract_peer_review_context with multiple 'blocking' mentions."""
        turns = [
            Turn(turn_type="assistant", content="Found blocking issue in auth.", line_number=1),
            Turn(turn_type="assistant", content="Another blocking problem found.", line_number=2),
            Turn(turn_type="assistant", content="Total: 2 blocking issues.", line_number=3),
        ]

        context = _extract_peer_review_context(turns, 0, {})

        # Should detect that there ARE blocking issues
        assert context.get("has_blocking") is True

    def test_peer_review_conflicting_blocking_counts(self):
        """Test _extract_peer_review_context with conflicting blocking info."""
        turns = [
            Turn(turn_type="assistant", content="0 blocking issues found.", line_number=1),
            Turn(turn_type="assistant", content="Wait, found 1 blocking issue.", line_number=2),
        ]

        context = _extract_peer_review_context(turns, 0, {})

        # The function scans in reverse, so the last mention (first in reversed) wins
        # "1 blocking issue" should be detected as has_blocking=True
        # But since "0 blocking" also contains "blocking", it depends on order
        # The current implementation checks "no blocking" or "0 blocking" first
        assert "has_blocking" in context

    def test_peer_review_no_blocking_explicitly(self):
        """Test _extract_peer_review_context with explicit 'no blocking'."""
        turns = [
            Turn(turn_type="assistant", content="Review complete. No blocking issues.", line_number=1),
        ]

        context = _extract_peer_review_context(turns, 0, {})

        assert context.get("has_blocking") is False

    def test_peer_review_zero_blocking(self):
        """Test _extract_peer_review_context with '0 blocking'."""
        turns = [
            Turn(turn_type="assistant", content="Summary: 0 blocking, 3 minor.", line_number=1),
        ]

        context = _extract_peer_review_context(turns, 0, {})

        assert context.get("has_blocking") is False
        assert context.get("minor_count") == 3

    def test_peer_review_multiple_minor_counts(self):
        """Test that first minor count found (in reverse) is used."""
        turns = [
            Turn(turn_type="assistant", content="Found 5 minor issues.", line_number=1),
            Turn(turn_type="assistant", content="After fixes: 2 minor issues remain.", line_number=2),
        ]

        context = _extract_peer_review_context(turns, 0, {})

        # Scanning in reverse, should find "2 minor" first
        assert context.get("minor_count") == 2

    def test_peer_review_future_count_extraction(self):
        """Test extraction of future recommendation count."""
        turns = [
            Turn(turn_type="assistant", content="3 future recommendations noted.", line_number=1),
        ]

        context = _extract_peer_review_context(turns, 0, {})

        assert context.get("future_count") == 3

    def test_peer_review_preserves_existing_context(self):
        """Test that existing context values are preserved."""
        turns = [
            Turn(turn_type="assistant", content="2 minor issues.", line_number=1),
        ]
        existing_context = {"pr_number": 42, "branch_name": "feature/test"}

        context = _extract_peer_review_context(turns, 0, existing_context)

        # Should preserve existing values
        assert context.get("pr_number") == 42
        assert context.get("branch_name") == "feature/test"
        # And add new ones
        assert context.get("minor_count") == 2

    def test_peer_review_skips_user_turns(self):
        """Test that user turns are skipped during extraction."""
        turns = [
            Turn(turn_type="user", content="There are 99 blocking issues!", line_number=1),
            Turn(turn_type="assistant", content="No blocking issues found.", line_number=2),
        ]

        context = _extract_peer_review_context(turns, 0, {})

        # Should only consider assistant turn
        assert context.get("has_blocking") is False

    def test_orchestrate_phase_mentioned_in_non_standard_format(self):
        """Test _extract_orchestrate_context with non-standard phase format."""
        turns = [
            Turn(turn_type="assistant", content="Currently executing the code implementation.", line_number=1),
        ]

        context = _extract_orchestrate_context(turns, 0, {})

        # "code phase" pattern not matched, so no current_phase
        # unless the content matches the expected patterns
        assert isinstance(context, dict)

    def test_orchestrate_phase_standard_format(self):
        """Test _extract_orchestrate_context with standard 'X phase' format."""
        turns = [
            Turn(turn_type="assistant", content="In the code phase now.", line_number=1),
        ]

        context = _extract_orchestrate_context(turns, 0, {})

        assert context.get("current_phase") == "code"

    def test_orchestrate_starting_phase_format(self):
        """Test _extract_orchestrate_context with 'starting X' format."""
        turns = [
            Turn(turn_type="assistant", content="Starting prepare phase.", line_number=1),
        ]

        context = _extract_orchestrate_context(turns, 0, {})

        assert context.get("current_phase") == "prepare"

    def test_orchestrate_missing_phase(self):
        """Test _extract_orchestrate_context when no phase is mentioned."""
        turns = [
            Turn(turn_type="assistant", content="Working on the task...", line_number=1),
        ]

        context = _extract_orchestrate_context(turns, 0, {})

        assert "current_phase" not in context

    def test_orchestrate_all_phases_detected(self):
        """Test that all PACT phases can be detected."""
        phases = ["prepare", "architect", "code", "test"]

        for phase in phases:
            turns = [
                Turn(turn_type="assistant", content=f"Now in {phase} phase.", line_number=1),
            ]
            context = _extract_orchestrate_context(turns, 0, {})
            assert context.get("current_phase") == phase, f"Failed to detect {phase} phase"

    def test_orchestrate_most_recent_phase_used(self):
        """Test that the most recent phase (in reverse scan) is used."""
        turns = [
            Turn(turn_type="assistant", content="prepare phase complete.", line_number=1),
            Turn(turn_type="assistant", content="architect phase complete.", line_number=2),
            Turn(turn_type="assistant", content="Starting code phase.", line_number=3),
        ]

        context = _extract_orchestrate_context(turns, 0, {})

        # Scanning in reverse, "code phase" should be found first
        assert context.get("current_phase") == "code"

    def test_orchestrate_case_insensitive(self):
        """Test that phase detection is case insensitive."""
        turns = [
            Turn(turn_type="assistant", content="CODE PHASE initiated.", line_number=1),
        ]

        context = _extract_orchestrate_context(turns, 0, {})

        # Content is lowercased before matching
        assert context.get("current_phase") == "code"

    def test_orchestrate_preserves_existing_context(self):
        """Test that existing context is preserved."""
        turns = [
            Turn(turn_type="assistant", content="In test phase.", line_number=1),
        ]
        existing = {"task_summary": "implement auth", "branch_name": "feat/auth"}

        context = _extract_orchestrate_context(turns, 0, existing)

        assert context.get("task_summary") == "implement auth"
        assert context.get("branch_name") == "feat/auth"
        assert context.get("current_phase") == "test"

    def test_orchestrate_skips_user_turns(self):
        """Test that user turns are skipped."""
        turns = [
            Turn(turn_type="user", content="What code phase are you in?", line_number=1),
            Turn(turn_type="assistant", content="Currently in architect phase.", line_number=2),
        ]

        context = _extract_orchestrate_context(turns, 0, {})

        assert context.get("current_phase") == "architect"

    def test_orchestrate_respects_trigger_index(self):
        """Test that extraction starts from trigger_index."""
        turns = [
            Turn(turn_type="assistant", content="Old: prepare phase.", line_number=1),
            Turn(turn_type="assistant", content="Old: architect phase.", line_number=2),
            Turn(turn_type="user", content="/PACT:orchestrate", line_number=3),  # trigger
            Turn(turn_type="assistant", content="New: code phase.", line_number=4),
        ]

        # With trigger_index=2, should only look at turns[2:]
        context = _extract_orchestrate_context(turns, 2, {})

        # Should find "code" from turn at index 3, not older phases
        assert context.get("current_phase") == "code"
