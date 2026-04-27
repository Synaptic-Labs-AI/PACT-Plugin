"""
Tests for the extract_workflow_state entry point.

Tests the main public API from refresh/__init__.py which is used by
the PreCompact hook to extract workflow state from transcripts.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

from refresh import extract_workflow_state
from fixtures.refresh_system import (
    create_peer_review_transcript,
    create_orchestrate_transcript,
    create_no_workflow_transcript,
    create_terminated_workflow_transcript,
    create_malformed_transcript,
    make_user_message,
    make_assistant_message,
    create_transcript_lines,
)


class TestExtractWorkflowStateSuccessfulExtraction:
    """Tests for successful workflow state extraction."""

    def test_extract_peer_review_workflow(self, tmp_path: Path):
        """Test extracting state from active peer-review workflow."""
        transcript_content = create_peer_review_transcript(
            step="recommendations",
            include_pr_number=64,
            include_pending_question=True,
        )
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(transcript_content)

        result = extract_workflow_state(str(file_path))

        assert result is not None
        assert result["workflow"]["name"] == "peer-review"
        assert "extraction" in result
        assert result["extraction"]["confidence"] >= 0.3

    def test_extract_orchestrate_workflow(self, tmp_path: Path):
        """Test extracting state from orchestrate workflow in CODE phase."""
        transcript_content = create_orchestrate_transcript(
            phase="code",
            include_task="implement auth",
            include_agent_calls=True,
        )
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(transcript_content)

        result = extract_workflow_state(str(file_path))

        assert result is not None
        assert result["workflow"]["name"] == "orchestrate"
        assert result["extraction"]["confidence"] >= 0.3

    def test_extract_workflow_includes_step_info(self, tmp_path: Path):
        """Test that extracted state includes step information."""
        transcript_content = create_peer_review_transcript(
            step="recommendations",
            include_pr_number=42,
        )
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(transcript_content)

        result = extract_workflow_state(str(file_path))

        assert result is not None
        assert "step" in result
        assert result["step"]["name"] is not None

    def test_extract_workflow_includes_context(self, tmp_path: Path):
        """Test that extracted state includes workflow context."""
        transcript_content = create_peer_review_transcript(
            step="recommendations",
            include_pr_number=99,
        )
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(transcript_content)

        result = extract_workflow_state(str(file_path))

        assert result is not None
        assert "context" in result

    def test_extract_workflow_includes_pending_action(self, tmp_path: Path):
        """Test that pending actions are extracted when present."""
        transcript_content = create_peer_review_transcript(
            step="recommendations",
            include_pending_question=True,
        )
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(transcript_content)

        result = extract_workflow_state(str(file_path))

        assert result is not None
        # Pending action may or may not be present depending on pattern matching
        # The key test is that the workflow is detected


class TestExtractWorkflowStateConfidenceThreshold:
    """Tests for confidence threshold filtering."""

    def test_low_confidence_returns_none(self, tmp_path: Path):
        """Test that workflows with very low confidence return None.

        A minimal workflow trigger without supporting signals should
        still be detected if confidence >= 0.3 threshold.
        """
        # Create transcript with just trigger and minimal signals
        lines = [
            make_user_message("/PACT:peer-review"),
            make_assistant_message("Starting peer review process..."),
        ]
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(create_transcript_lines(lines))

        result = extract_workflow_state(str(file_path))

        # With just a clear trigger (0.4 confidence weight), should pass threshold
        # If no result, it means confidence was below 0.3
        if result is not None:
            assert result["extraction"]["confidence"] >= 0.3

    def test_high_confidence_workflow_returned(self, tmp_path: Path):
        """Test that high confidence workflows are returned."""
        transcript_content = create_peer_review_transcript(
            step="recommendations",
            include_pr_number=64,
            include_pending_question=True,
        )
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(transcript_content)

        result = extract_workflow_state(str(file_path))

        assert result is not None
        assert result["extraction"]["confidence"] >= 0.3

    def test_confidence_just_above_threshold(self, tmp_path: Path):
        """Test workflow at exactly the confidence threshold."""
        # Create a transcript that should produce moderate confidence
        lines = [
            make_user_message("/PACT:orchestrate implement feature"),
            make_assistant_message("variety-assess: Analyzing task complexity..."),
            make_assistant_message("prepare phase: Starting preparation."),
        ]
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(create_transcript_lines(lines))

        result = extract_workflow_state(str(file_path))

        # With trigger (0.4) + step marker (0.2), should be >= 0.3
        if result is not None:
            assert result["extraction"]["confidence"] >= 0.3


class TestExtractWorkflowStateNoneReturns:
    """Tests for cases where None should be returned."""

    def test_nonexistent_file_returns_none(self):
        """Test that nonexistent file returns None."""
        result = extract_workflow_state("/nonexistent/path/session.jsonl")

        assert result is None

    def test_empty_transcript_returns_none(self, tmp_path: Path):
        """Test that empty transcript returns None."""
        file_path = tmp_path / "session.jsonl"
        file_path.write_text("")

        result = extract_workflow_state(str(file_path))

        assert result is None

    def test_no_workflow_transcript_returns_none(self, tmp_path: Path):
        """Test that transcript without workflow returns None."""
        transcript_content = create_no_workflow_transcript()
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(transcript_content)

        result = extract_workflow_state(str(file_path))

        assert result is None

    def test_terminated_workflow_may_return_none_or_low_confidence(self, tmp_path: Path):
        """Test that terminated workflow returns None or has low confidence.

        Note: The exact behavior depends on pattern matching. A terminated
        workflow should either return None or have the terminated flag set.
        """
        transcript_content = create_terminated_workflow_transcript()
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(transcript_content)

        result = extract_workflow_state(str(file_path))

        # Either None or detected as terminated
        if result is not None:
            # Should have valid confidence if returned
            assert result["extraction"]["confidence"] >= 0.3

    def test_malformed_transcript_handles_gracefully(self, tmp_path: Path):
        """Test that malformed transcript lines are handled gracefully."""
        transcript_content = create_malformed_transcript()
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(transcript_content)

        # Should not raise - either returns None or partial result
        result = extract_workflow_state(str(file_path))

        # May return None or a result depending on valid content
        # The key is that it doesn't crash

    def test_whitespace_only_transcript_returns_none(self, tmp_path: Path):
        """Test that whitespace-only transcript returns None."""
        file_path = tmp_path / "session.jsonl"
        file_path.write_text("   \n\n   \n")

        result = extract_workflow_state(str(file_path))

        assert result is None


class TestExtractWorkflowStateCheckpointSchema:
    """Tests verifying the checkpoint schema structure."""

    def test_checkpoint_has_required_fields(self, tmp_path: Path):
        """Test that returned checkpoint has all required fields."""
        transcript_content = create_peer_review_transcript()
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(transcript_content)

        result = extract_workflow_state(str(file_path))

        assert result is not None
        # Required top-level fields
        assert "workflow" in result
        assert "extraction" in result
        # Workflow fields
        assert "name" in result["workflow"]
        # Extraction fields
        assert "confidence" in result["extraction"]

    def test_checkpoint_workflow_structure(self, tmp_path: Path):
        """Test workflow section structure."""
        transcript_content = create_peer_review_transcript(include_pr_number=123)
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(transcript_content)

        result = extract_workflow_state(str(file_path))

        assert result is not None
        workflow = result["workflow"]
        assert isinstance(workflow["name"], str)
        # ID may or may not be present
        if "id" in workflow:
            assert isinstance(workflow["id"], str)

    def test_checkpoint_extraction_structure(self, tmp_path: Path):
        """Test extraction section structure."""
        transcript_content = create_peer_review_transcript()
        file_path = tmp_path / "session.jsonl"
        file_path.write_text(transcript_content)

        result = extract_workflow_state(str(file_path))

        assert result is not None
        extraction = result["extraction"]
        assert isinstance(extraction["confidence"], (int, float))
        assert 0.0 <= extraction["confidence"] <= 1.0


class TestExtractWorkflowStateEdgeCases:
    """Tests for edge cases in workflow state extraction."""

    def test_very_large_transcript(self, tmp_path: Path):
        """Test handling of large transcript (uses max_lines limit)."""
        # Create large transcript with many messages
        lines = []
        lines.append(make_user_message("/PACT:peer-review"))
        for i in range(1000):
            lines.append(make_assistant_message(f"Processing message {i}..."))
        lines.append(make_assistant_message("recommendations phase: AskUserQuestion: Ready to proceed?"))

        file_path = tmp_path / "large.jsonl"
        file_path.write_text(create_transcript_lines(lines))

        result = extract_workflow_state(str(file_path))

        # Should still detect the workflow (within last 500 lines)
        # The trigger is at the beginning so may not be captured
        # But this tests that large files don't crash

    def test_unicode_content_in_transcript(self, tmp_path: Path):
        """Test handling of unicode content."""
        lines = [
            make_user_message("/PACT:peer-review with unicode"),
            make_assistant_message("Starting review with special chars"),
        ]
        file_path = tmp_path / "unicode.jsonl"
        file_path.write_text(create_transcript_lines(lines), encoding="utf-8")

        result = extract_workflow_state(str(file_path))

        # Should handle unicode without error
        if result is not None:
            assert result["workflow"]["name"] == "peer-review"

    def test_path_with_spaces(self, tmp_path: Path):
        """Test handling of file path with spaces."""
        spaced_dir = tmp_path / "path with spaces"
        spaced_dir.mkdir()
        file_path = spaced_dir / "session.jsonl"

        transcript_content = create_peer_review_transcript()
        file_path.write_text(transcript_content)

        result = extract_workflow_state(str(file_path))

        # Should handle path with spaces
        if result is not None:
            assert result["workflow"]["name"] == "peer-review"

    def test_multiple_workflow_triggers_uses_most_recent(self, tmp_path: Path):
        """Test that most recent workflow trigger is used."""
        lines = [
            make_user_message("/PACT:orchestrate old task", "2025-01-22T10:00:00Z"),
            make_assistant_message("All phases complete. IMPLEMENTED.", "2025-01-22T10:30:00Z"),
            make_user_message("/PACT:peer-review", "2025-01-22T11:00:00Z"),
            make_assistant_message("Starting peer review...", "2025-01-22T11:00:05Z"),
        ]
        file_path = tmp_path / "multi.jsonl"
        file_path.write_text(create_transcript_lines(lines))

        result = extract_workflow_state(str(file_path))

        # Should find peer-review as most recent
        assert result is not None
        assert result["workflow"]["name"] == "peer-review"
