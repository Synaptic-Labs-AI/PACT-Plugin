"""
Tests for the checkpoint_builder module.

Tests checkpoint assembly, validation, and refresh message generation.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from refresh.checkpoint_builder import (
    CheckpointSchema,
    get_session_id,
    get_encoded_project_path,
    get_current_timestamp,
    build_checkpoint,
    build_no_workflow_checkpoint,
    validate_checkpoint,
    checkpoint_to_refresh_message,
)
from refresh.workflow_detector import WorkflowInfo
from refresh.step_extractor import StepInfo, PendingAction
from refresh.transcript_parser import Turn


class TestGetSessionId:
    """Tests for get_session_id function."""

    def test_returns_env_session_id(self):
        """Test that session ID is read from environment."""
        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session-abc"}):
            session_id = get_session_id()

        assert session_id == "test-session-abc"

    def test_returns_unknown_when_not_set(self):
        """Test default when CLAUDE_SESSION_ID not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove CLAUDE_SESSION_ID if it exists
            os.environ.pop("CLAUDE_SESSION_ID", None)
            session_id = get_session_id()

        assert session_id == "unknown"


class TestGetEncodedProjectPath:
    """Tests for get_encoded_project_path function."""

    def test_extract_from_transcript_path(self):
        """Test extracting encoded path from transcript path."""
        transcript_path = "/Users/test/.claude/projects/-Users-test-myproject/session-123/session.jsonl"

        encoded = get_encoded_project_path(transcript_path)

        assert encoded == "-Users-test-myproject"

    def test_extract_nested_project_path(self):
        """Test extracting deeply nested project path."""
        transcript_path = "/home/user/.claude/projects/-home-user-code-org-repo/uuid/session.jsonl"

        encoded = get_encoded_project_path(transcript_path)

        assert encoded == "-home-user-code-org-repo"

    def test_fallback_to_project_dir(self):
        """Test fallback to CLAUDE_PROJECT_DIR when extraction fails."""
        transcript_path = "/invalid/path/without/projects"

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/Users/test/myproject"}):
            encoded = get_encoded_project_path(transcript_path)

        # Now keeps the leading dash to match Claude Code's folder naming convention
        assert encoded == "-Users-test-myproject"

    def test_fallback_unknown_project(self):
        """Test fallback to 'unknown-project' when all else fails."""
        transcript_path = "/invalid/path"

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            encoded = get_encoded_project_path(transcript_path)

        assert encoded == "unknown-project"


class TestGetCurrentTimestamp:
    """Tests for get_current_timestamp function."""

    def test_returns_iso_format(self):
        """Test that timestamp is in ISO format."""
        timestamp = get_current_timestamp()

        # Should be parseable as ISO
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        assert parsed is not None

    def test_is_utc(self):
        """Test that timestamp is in UTC."""
        timestamp = get_current_timestamp()

        # Should contain timezone info
        assert "+" in timestamp or "Z" in timestamp


class TestBuildCheckpoint:
    """Tests for build_checkpoint function."""

    @pytest.fixture
    def sample_workflow_info(self) -> WorkflowInfo:
        """Create sample WorkflowInfo for testing."""
        trigger = Turn(turn_type="user", content="/PACT:peer-review", line_number=1)
        return WorkflowInfo(
            name="peer-review",
            workflow_id="pr-64",
            started_at="2025-01-22T12:00:00Z",
            trigger_turn=trigger,
            confidence=0.85,
            is_terminated=False,
            notes="clear trigger, step: recommendations",
        )

    @pytest.fixture
    def sample_step_info(self) -> StepInfo:
        """Create sample StepInfo for testing."""
        return StepInfo(
            name="recommendations",
            sequence=5,
            started_at="2025-01-22T12:05:00Z",
            pending_action=PendingAction(
                action_type="AskUserQuestion",
                instruction="Would you like to review?",
                data={},
            ),
            context={
                "pr_number": 64,
                "has_blocking": False,
            },
        )

    def test_build_complete_checkpoint(self, sample_workflow_info, sample_step_info):
        """Test building checkpoint with all fields."""
        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session"}):
            checkpoint = build_checkpoint(
                transcript_path="/test/path/session.jsonl",
                workflow_info=sample_workflow_info,
                step_info=sample_step_info,
                lines_scanned=150,
            )

        assert checkpoint["version"] == "1.0"
        assert checkpoint["session_id"] == "test-session"
        assert checkpoint["workflow"]["name"] == "peer-review"
        assert checkpoint["workflow"]["id"] == "pr-64"
        assert checkpoint["step"]["name"] == "recommendations"
        assert checkpoint["step"]["sequence"] == 5
        assert checkpoint["pending_action"]["type"] == "AskUserQuestion"
        assert checkpoint["context"]["pr_number"] == 64
        assert checkpoint["extraction"]["confidence"] == 0.85
        assert checkpoint["extraction"]["transcript_lines_scanned"] == 150
        assert "created_at" in checkpoint

    def test_build_checkpoint_no_pending_action(self, sample_workflow_info):
        """Test building checkpoint without pending action."""
        step_info = StepInfo(
            name="commit",
            sequence=1,
            started_at="2025-01-22T12:00:00Z",
            pending_action=None,
            context={},
        )

        checkpoint = build_checkpoint(
            transcript_path="/test/path",
            workflow_info=sample_workflow_info,
            step_info=step_info,
            lines_scanned=100,
        )

        assert checkpoint["pending_action"] is None

    def test_build_checkpoint_terminated_workflow(self, sample_step_info):
        """Test building checkpoint for terminated workflow."""
        workflow_info = WorkflowInfo(
            name="peer-review",
            is_terminated=True,
            confidence=0.9,
            notes="Workflow completed",
        )

        checkpoint = build_checkpoint(
            transcript_path="/test/path",
            workflow_info=workflow_info,
            step_info=sample_step_info,
            lines_scanned=100,
        )

        # Terminated workflows set name to "none"
        assert checkpoint["workflow"]["name"] == "none"
        assert checkpoint["extraction"]["notes"] == "Workflow terminated"


class TestBuildNoWorkflowCheckpoint:
    """Tests for build_no_workflow_checkpoint function."""

    def test_build_no_workflow_checkpoint(self):
        """Test building checkpoint when no workflow detected."""
        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session"}):
            checkpoint = build_no_workflow_checkpoint(
                transcript_path="/test/path",
                lines_scanned=500,
                reason="No PACT trigger found",
            )

        assert checkpoint["version"] == "1.0"
        assert checkpoint["workflow"]["name"] == "none"
        assert checkpoint["workflow"]["id"] == ""
        assert checkpoint["step"]["name"] == ""
        assert checkpoint["step"]["sequence"] == 0
        assert checkpoint["pending_action"] is None
        assert checkpoint["context"] == {}
        assert checkpoint["extraction"]["confidence"] == 1.0
        assert checkpoint["extraction"]["notes"] == "No PACT trigger found"
        assert checkpoint["extraction"]["transcript_lines_scanned"] == 500

    def test_build_no_workflow_default_reason(self):
        """Test default reason message."""
        checkpoint = build_no_workflow_checkpoint(
            transcript_path="/test/path",
            lines_scanned=100,
        )

        assert checkpoint["extraction"]["notes"] == "No active workflow detected"


class TestValidateCheckpoint:
    """Tests for validate_checkpoint function."""

    def test_validate_complete_checkpoint(self, sample_checkpoint):
        """Test validating a complete valid checkpoint."""
        is_valid, error = validate_checkpoint(sample_checkpoint)

        assert is_valid is True
        assert error == ""

    def test_validate_missing_version(self, sample_checkpoint):
        """Test validation fails without version."""
        del sample_checkpoint["version"]

        is_valid, error = validate_checkpoint(sample_checkpoint)

        assert is_valid is False
        assert "version" in error

    def test_validate_missing_session_id(self, sample_checkpoint):
        """Test validation fails without session_id."""
        del sample_checkpoint["session_id"]

        is_valid, error = validate_checkpoint(sample_checkpoint)

        assert is_valid is False
        assert "session_id" in error

    def test_validate_missing_workflow(self, sample_checkpoint):
        """Test validation fails without workflow."""
        del sample_checkpoint["workflow"]

        is_valid, error = validate_checkpoint(sample_checkpoint)

        assert is_valid is False
        assert "workflow" in error

    def test_validate_missing_workflow_name(self, sample_checkpoint):
        """Test validation fails without workflow.name."""
        del sample_checkpoint["workflow"]["name"]

        is_valid, error = validate_checkpoint(sample_checkpoint)

        assert is_valid is False
        assert "workflow.name" in error

    def test_validate_missing_extraction(self, sample_checkpoint):
        """Test validation fails without extraction."""
        del sample_checkpoint["extraction"]

        is_valid, error = validate_checkpoint(sample_checkpoint)

        assert is_valid is False
        assert "extraction" in error

    def test_validate_missing_confidence(self, sample_checkpoint):
        """Test validation fails without extraction.confidence."""
        del sample_checkpoint["extraction"]["confidence"]

        is_valid, error = validate_checkpoint(sample_checkpoint)

        assert is_valid is False
        assert "confidence" in error

    def test_validate_empty_checkpoint(self):
        """Test validation fails for empty dict."""
        is_valid, error = validate_checkpoint({})

        assert is_valid is False


class TestCheckpointToRefreshMessage:
    """Tests for checkpoint_to_refresh_message function."""

    def test_refresh_message_format(self, sample_checkpoint):
        """Test directive prompt refresh message contains expected elements."""
        message = checkpoint_to_refresh_message(sample_checkpoint)

        # Check new directive prompt format
        assert "[POST-COMPACTION CHECKPOINT]" in message
        assert "Prior conversation auto-compacted" in message
        assert "Resume unfinished PACT workflow below:" in message
        assert "Workflow:" in message
        assert "peer-review" in message
        assert "pr-64" in message
        assert "Context:" in message
        # Check prose context is included (recommendations step generates prose)
        assert "recommendations" in message.lower() or "Processing" in message
        # High confidence (0.9) should NOT show low confidence suffix
        assert "low confidence" not in message
        assert "verify state with user" not in message.lower()

    def test_refresh_message_with_pending_action(self, sample_checkpoint):
        """Test refresh message includes pending action as Next step line."""
        message = checkpoint_to_refresh_message(sample_checkpoint)

        assert "Next Step:" in message
        assert "Would you like to review" in message

    def test_refresh_message_with_context_prose(self, sample_checkpoint):
        """Test refresh message includes prose context (not key=value)."""
        message = checkpoint_to_refresh_message(sample_checkpoint)

        # Should have Context line with prose, not key=value format
        assert "Context:" in message
        # The recommendations step should generate prose like:
        # "Processing recommendations; no blocking issues, 0 minor, 1 future."
        assert "no blocking" in message.lower() or "Processing" in message

    def test_refresh_message_no_workflow(self):
        """Test no refresh message for 'none' workflow."""
        checkpoint = {
            "workflow": {"name": "none"},
        }

        message = checkpoint_to_refresh_message(checkpoint)

        assert message == ""

    def test_refresh_message_low_confidence_no_action_shows_ask_user(self):
        """Test low confidence with no action shows ask user message."""
        checkpoint = {
            "workflow": {
                "name": "peer-review",
                "id": "",
            },
            "step": {"name": "commit"},
            "extraction": {"confidence": 0.5},
            "context": {},
            "pending_action": None,
        }

        message = checkpoint_to_refresh_message(checkpoint)

        # No pending action should always show "Ask user how to proceed"
        assert "Next Step: **Ask user how to proceed.**" in message

    def test_refresh_message_high_confidence_no_action_shows_ask_user(self):
        """Test high confidence with no action shows ask user message."""
        checkpoint = {
            "workflow": {
                "name": "peer-review",
                "id": "",
            },
            "step": {"name": "commit"},
            "extraction": {"confidence": 0.9},
            "context": {},
            "pending_action": None,
        }

        message = checkpoint_to_refresh_message(checkpoint)

        # High confidence (>= 0.8) should NOT show low confidence suffix
        assert "low confidence" not in message
        assert "verify state with user" not in message.lower()
        # No pending action should always show "Ask user how to proceed"
        assert "Next Step: **Ask user how to proceed.**" in message

    def test_refresh_message_no_pending_action_shows_ask_user(self):
        """Test refresh message without pending action shows ask user message."""
        checkpoint = {
            "workflow": {
                "name": "orchestrate",
                "id": "",
            },
            "step": {"name": "code"},
            "extraction": {"confidence": 0.7},
            "context": {},
            "pending_action": None,
        }

        message = checkpoint_to_refresh_message(checkpoint)

        assert "orchestrate" in message
        # No pending action should always show "Ask user how to proceed"
        assert "Next Step: **Ask user how to proceed.**" in message

    def test_refresh_message_empty_context_still_shows_prose(self):
        """Test refresh message with empty context still shows prose Context line."""
        checkpoint = {
            "workflow": {
                "name": "orchestrate",
                "id": "",
            },
            "step": {"name": "code"},
            "extraction": {"confidence": 0.9},  # High confidence - no warning
            "context": {},
            "pending_action": None,
        }

        message = checkpoint_to_refresh_message(checkpoint)

        # Even with empty context, we now show a prose Context line
        assert "Context:" in message
        # Should have prose like "Was running CODE phase."
        assert "CODE phase" in message or "code" in message.lower()
        assert isinstance(message, str)
        # Should still have required lines
        assert "[POST-COMPACTION CHECKPOINT]" in message
        assert "Workflow:" in message
        # High confidence - no low confidence suffix
        assert "low confidence" not in message
        # No pending action - should show ask user message
        assert "Next Step: **Ask user how to proceed.**" in message

    def test_refresh_message_directive_format_high_confidence(self):
        """Test the exact directive prompt format structure with high confidence (no warning)."""
        checkpoint = {
            "workflow": {
                "name": "peer-review",
                "id": "PR#88",
            },
            "step": {"name": "awaiting_user_decision"},
            "extraction": {"confidence": 0.9},  # High confidence - no low confidence suffix
            "context": {"reviewers": 3, "blocking": 0},
            "pending_action": {
                "type": "User Decision",
                "instruction": "Waiting for user to authorize merge",
            },
        }

        message = checkpoint_to_refresh_message(checkpoint)
        lines = message.split("\n")

        # Should have 5 lines (header, explanatory, workflow, context, next step) - NO low confidence suffix
        assert len(lines) == 5
        # Line 1: [POST-COMPACTION CHECKPOINT]
        assert lines[0] == "[POST-COMPACTION CHECKPOINT]"
        # Line 2: Shorter explanatory line
        assert lines[1] == "Prior conversation auto-compacted. Resume unfinished PACT workflow below:"
        # Line 3: Workflow: workflow (id)
        assert lines[2] == "Workflow: peer-review (PR#88)"
        # Line 4: Prose Context
        assert lines[3].startswith("Context:")
        assert "waiting for user decision" in lines[3].lower()
        # Line 5: Next step (no low confidence suffix)
        assert lines[4] == "Next Step: Waiting for user to authorize merge"

    def test_refresh_message_directive_format_low_confidence(self):
        """Test the exact directive prompt format structure with low confidence (shows suffix)."""
        checkpoint = {
            "workflow": {
                "name": "peer-review",
                "id": "PR#88",
            },
            "step": {"name": "awaiting_user_decision"},
            "extraction": {"confidence": 0.6},  # Low confidence - shows suffix on Next step
            "context": {"reviewers": 3, "blocking": 0},
            "pending_action": {
                "type": "User Decision",
                "instruction": "Waiting for user to authorize merge",
            },
        }

        message = checkpoint_to_refresh_message(checkpoint)
        lines = message.split("\n")

        # Should have 5 lines (header, explanatory, workflow, context, next step with suffix)
        assert len(lines) == 5
        # Line 1: [POST-COMPACTION CHECKPOINT]
        assert lines[0] == "[POST-COMPACTION CHECKPOINT]"
        # Line 2: Shorter explanatory line
        assert lines[1] == "Prior conversation auto-compacted. Resume unfinished PACT workflow below:"
        # Line 3: Workflow: workflow (id)
        assert lines[2] == "Workflow: peer-review (PR#88)"
        # Line 4: Prose Context
        assert lines[3].startswith("Context:")
        assert "waiting for user decision" in lines[3].lower()
        # Line 5: Next step with low confidence suffix
        assert lines[4] == "Next Step: Waiting for user to authorize merge. **Get user approval before acting.**"

    def test_refresh_message_invoke_reviewers_prose(self):
        """Test prose context for invoke-reviewers step."""
        checkpoint = {
            "workflow": {"name": "peer-review", "id": "pr-88"},
            "step": {"name": "invoke-reviewers"},
            "extraction": {"confidence": 0.9},
            "context": {"reviewers": "2/3", "blocking": "0"},
            "pending_action": None,
        }

        message = checkpoint_to_refresh_message(checkpoint)

        assert "Context:" in message
        # Should have prose like "Launched 3 reviewer agents; 2 had completed with 0 blocking issues."
        assert "Launched" in message
        assert "3" in message
        assert "2" in message
        assert "blocking" in message.lower()

    def test_refresh_message_merge_ready_prose(self):
        """Test prose context for merge-ready step."""
        checkpoint = {
            "workflow": {"name": "peer-review", "id": "pr-42"},
            "step": {"name": "merge-ready"},
            "extraction": {"confidence": 0.9},
            "context": {"blocking": 0},
            "pending_action": None,
        }

        message = checkpoint_to_refresh_message(checkpoint)

        assert "Context:" in message
        # Should have prose like "Completed review with no blocking issues; PR ready for merge."
        assert "no blocking" in message.lower() or "ready for merge" in message.lower()

    def test_refresh_message_unknown_step_fallback(self):
        """Test prose context fallback for unknown step."""
        checkpoint = {
            "workflow": {"name": "peer-review", "id": "pr-99"},
            "step": {"name": "some-unknown-step"},
            "extraction": {"confidence": 0.9},
            "context": {"foo": "bar", "baz": 123},
            "pending_action": None,
        }

        message = checkpoint_to_refresh_message(checkpoint)

        assert "Context:" in message
        # Should fall back to generic format with key=value
        assert "some-unknown-step" in message
        assert "foo=bar" in message


class TestEdgeCases:
    """Tests for edge cases in checkpoint building."""

    def test_checkpoint_handles_unicode(self):
        """Test checkpoint handles unicode content."""
        workflow_info = WorkflowInfo(
            name="peer-review",
            notes="Unicode test: ",
        )
        step_info = StepInfo(
            name="test",
            context={"summary": "Testing  content"},
        )

        checkpoint = build_checkpoint(
            transcript_path="/test/path",
            workflow_info=workflow_info,
            step_info=step_info,
            lines_scanned=10,
        )

        assert "" in checkpoint["context"]["summary"]

    def test_checkpoint_handles_special_characters(self):
        """Test checkpoint handles special characters in content."""
        step_info = StepInfo(
            name="test",
            pending_action=PendingAction(
                action_type="Test",
                instruction="Test with \"quotes\" and 'apostrophes'",
            ),
        )
        workflow_info = WorkflowInfo(name="test")

        checkpoint = build_checkpoint(
            transcript_path="/test/path",
            workflow_info=workflow_info,
            step_info=step_info,
            lines_scanned=10,
        )

        # Should be JSON serializable
        json_str = json.dumps(checkpoint)
        assert "quotes" in json_str

    def test_checkpoint_serializable(self, sample_checkpoint):
        """Test that checkpoint is fully JSON serializable."""
        # Should not raise
        json_str = json.dumps(sample_checkpoint)
        parsed = json.loads(json_str)

        assert parsed == sample_checkpoint

    def test_refresh_message_handles_missing_fields(self):
        """Test refresh message handles missing optional fields gracefully."""
        # Minimal checkpoint with only required fields
        checkpoint = {
            "workflow": {"name": "peer-review"},
            "step": {},
            "extraction": {"confidence": 0.5},
        }

        # Should not raise
        message = checkpoint_to_refresh_message(checkpoint)

        assert "[POST-COMPACTION CHECKPOINT]" in message
        assert "peer-review" in message
        assert "unknown" in message  # Default step name
        # No pending action should show "Ask user how to proceed"
        assert "Next Step: **Ask user how to proceed.**" in message


class TestCheckpointSchemaRoundTrip:
    """Tests for CheckpointSchema to_dict and from_dict round-trip."""

    def test_round_trip_complete_schema(self, sample_checkpoint):
        """Test from_dict(to_dict(schema)) == schema for complete checkpoint."""
        # Create schema from sample checkpoint
        schema = CheckpointSchema.from_dict(sample_checkpoint)

        # Round-trip: schema -> dict -> schema
        exported = schema.to_dict()
        reimported = CheckpointSchema.from_dict(exported)

        # Compare key fields
        assert reimported.version == schema.version
        assert reimported.session_id == schema.session_id
        assert reimported.workflow_name == schema.workflow_name
        assert reimported.workflow_id == schema.workflow_id
        assert reimported.step_name == schema.step_name
        assert reimported.step_sequence == schema.step_sequence
        assert reimported.pending_action_type == schema.pending_action_type
        assert reimported.pending_action_instruction == schema.pending_action_instruction
        assert reimported.context == schema.context
        assert reimported.confidence == schema.confidence

    def test_round_trip_no_pending_action(self):
        """Test round-trip when pending_action is None."""
        checkpoint = {
            "version": "1.0",
            "session_id": "test-123",
            "workflow": {"name": "orchestrate", "id": "", "started_at": ""},
            "step": {"name": "code", "sequence": 3, "started_at": ""},
            "pending_action": None,
            "context": {},
            "extraction": {"confidence": 0.8, "notes": "", "transcript_lines_scanned": 100},
            "created_at": "2025-01-22T12:00:00Z",
        }

        schema = CheckpointSchema.from_dict(checkpoint)
        exported = schema.to_dict()

        assert exported["pending_action"] is None
        assert schema.pending_action_type is None

        # Round-trip
        reimported = CheckpointSchema.from_dict(exported)
        assert reimported.pending_action_type is None

    def test_from_dict_missing_optional_fields(self):
        """Test from_dict handles missing optional fields."""
        minimal_checkpoint = {
            "version": "1.0",
            "session_id": "test-123",
            "workflow": {"name": "peer-review"},
            "extraction": {"confidence": 0.5},
        }

        schema = CheckpointSchema.from_dict(minimal_checkpoint)

        # Should use defaults for missing fields
        assert schema.version == "1.0"
        assert schema.session_id == "test-123"
        assert schema.workflow_name == "peer-review"
        assert schema.workflow_id == ""  # Default
        assert schema.workflow_started_at == ""  # Default
        assert schema.step_name == ""  # Default
        assert schema.step_sequence == 0  # Default
        assert schema.pending_action_type is None  # Default
        assert schema.context == {}  # Default
        assert schema.confidence == 0.5
        assert schema.extraction_notes == ""  # Default
        assert schema.created_at == ""  # Default

    def test_from_dict_with_none_pending_action(self):
        """Test from_dict with explicit None for pending_action."""
        checkpoint = {
            "version": "1.0",
            "session_id": "test",
            "workflow": {"name": "test"},
            "step": {"name": "step1"},
            "pending_action": None,
            "context": {},
            "extraction": {"confidence": 0.7},
        }

        schema = CheckpointSchema.from_dict(checkpoint)

        assert schema.pending_action_type is None
        assert schema.pending_action_instruction is None
        assert schema.pending_action_data == {}

    def test_from_dict_empty_dict_uses_defaults(self):
        """Test from_dict with empty dict uses sensible defaults."""
        schema = CheckpointSchema.from_dict({})

        # Should use defaults without raising
        assert schema.version == "1.0"
        assert schema.session_id == ""
        assert schema.workflow_name == "none"
        assert schema.step_name == ""
        assert schema.confidence == 0.0

    def test_to_dict_creates_proper_structure(self):
        """Test to_dict creates the expected nested structure."""
        schema = CheckpointSchema(
            version="1.0",
            session_id="session-abc",
            workflow_name="peer-review",
            workflow_id="pr-42",
            workflow_started_at="2025-01-22T10:00:00Z",
            step_name="synthesize",
            step_sequence=4,
            step_started_at="2025-01-22T10:30:00Z",
            pending_action_type="AskUserQuestion",
            pending_action_instruction="Continue?",
            pending_action_data={"key": "value"},
            context={"pr_number": 42},
            confidence=0.85,
            extraction_notes="test notes",
            transcript_lines_scanned=200,
            created_at="2025-01-22T11:00:00Z",
        )

        result = schema.to_dict()

        # Verify structure
        assert result["version"] == "1.0"
        assert result["session_id"] == "session-abc"

        assert result["workflow"]["name"] == "peer-review"
        assert result["workflow"]["id"] == "pr-42"
        assert result["workflow"]["started_at"] == "2025-01-22T10:00:00Z"

        assert result["step"]["name"] == "synthesize"
        assert result["step"]["sequence"] == 4
        assert result["step"]["started_at"] == "2025-01-22T10:30:00Z"

        assert result["pending_action"]["type"] == "AskUserQuestion"
        assert result["pending_action"]["instruction"] == "Continue?"
        assert result["pending_action"]["data"] == {"key": "value"}

        assert result["context"] == {"pr_number": 42}

        assert result["extraction"]["confidence"] == 0.85
        assert result["extraction"]["notes"] == "test notes"
        assert result["extraction"]["transcript_lines_scanned"] == 200

        assert result["created_at"] == "2025-01-22T11:00:00Z"

    def test_to_dict_no_pending_action(self):
        """Test to_dict when pending_action_type is None."""
        schema = CheckpointSchema(
            version="1.0",
            session_id="test",
            workflow_name="orchestrate",
            pending_action_type=None,
        )

        result = schema.to_dict()

        assert result["pending_action"] is None

    def test_round_trip_with_complex_context(self):
        """Test round-trip preserves complex context data."""
        checkpoint = {
            "version": "1.0",
            "session_id": "test",
            "workflow": {"name": "peer-review", "id": "pr-99", "started_at": ""},
            "step": {"name": "recommendations", "sequence": 5, "started_at": ""},
            "pending_action": {
                "type": "UserDecision",
                "instruction": "Choose option",
                "data": {"options": ["A", "B", "C"], "nested": {"key": 123}},
            },
            "context": {
                "pr_number": 99,
                "has_blocking": True,
                "minor_count": 3,
                "future_count": 2,
                "tags": ["review", "test"],
            },
            "extraction": {"confidence": 0.95, "notes": "complex test", "transcript_lines_scanned": 500},
            "created_at": "2025-01-22T12:00:00Z",
        }

        schema = CheckpointSchema.from_dict(checkpoint)
        exported = schema.to_dict()

        # Verify complex data preserved
        assert exported["pending_action"]["data"]["options"] == ["A", "B", "C"]
        assert exported["pending_action"]["data"]["nested"]["key"] == 123
        assert exported["context"]["tags"] == ["review", "test"]
        assert exported["context"]["pr_number"] == 99


class TestImPACTResolutionPathIntegration:
    """Integration test: checkpoint_to_refresh_message → _prose_resolution_path flow."""

    @pytest.mark.parametrize("outcome,expected_fragment", [
        ("redo_prior_phase", "redo prior phase"),
        ("augment_present_phase", "augment"),
        ("invoke_repact", "repact"),
        ("terminate_agent", "terminate"),
        ("not_truly_blocked", "not truly blocked"),
        ("escalate_to_user", "escalate"),
    ])
    def test_checkpoint_resolution_path_produces_correct_prose(self, outcome, expected_fragment):
        """Full integration: imPACT checkpoint with resolution-path step produces correct prose."""
        checkpoint = {
            "workflow": {"name": "imPACT", "id": "impact-001"},
            "step": {"name": "resolution-path"},
            "extraction": {"confidence": 0.85},
            "context": {"outcome": outcome},
            "pending_action": {
                "instruction": "Continue with resolution",
            },
        }

        message = checkpoint_to_refresh_message(checkpoint)

        assert "Workflow: imPACT (impact-001)" in message
        assert expected_fragment in message.lower()
        assert "Next Step: Continue with resolution" in message

    def test_checkpoint_resolution_path_default_for_unknown_outcome(self):
        """Integration: unknown outcome falls through to default prose."""
        checkpoint = {
            "workflow": {"name": "imPACT", "id": ""},
            "step": {"name": "resolution-path"},
            "extraction": {"confidence": 0.7},
            "context": {"outcome": "some_future_outcome"},
            "pending_action": None,
        }

        message = checkpoint_to_refresh_message(checkpoint)

        assert "resolution path" in message.lower() or "blocker" in message.lower()
        assert "Ask user how to proceed" in message
