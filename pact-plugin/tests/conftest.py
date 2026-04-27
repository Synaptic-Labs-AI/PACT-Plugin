"""
Shared test fixtures for the refresh system tests.

Provides factories for generating realistic JSONL transcripts and
common fixtures used across multiple test modules.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# Add tests directory to path for helpers module imports
sys.path.insert(0, str(Path(__file__).parent))

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

# Add pact-memory scripts to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'pact-memory'))


# =============================================================================
# Transcript Line Factories
# =============================================================================

def make_user_message(
    content: str,
    timestamp: str | None = None,
    session_id: str = "test-session-123",
) -> dict[str, Any]:
    """
    Create a user message line for JSONL transcript.

    Args:
        content: The user's message text
        timestamp: ISO timestamp (generated if not provided)
        session_id: Session ID for the message

    Returns:
        Dict suitable for JSON serialization as JSONL line
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()

    return {
        "type": "user",
        "sessionId": session_id,
        "message": {
            "role": "user",
            "content": content,
        },
        "timestamp": timestamp,
    }


def make_assistant_message(
    content: str | list[dict[str, Any]],
    timestamp: str | None = None,
) -> dict[str, Any]:
    """
    Create an assistant message line for JSONL transcript.

    Args:
        content: Text content or list of content blocks
        timestamp: ISO timestamp (generated if not provided)

    Returns:
        Dict suitable for JSON serialization as JSONL line
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()

    # Handle string content
    if isinstance(content, str):
        content_blocks = [{"type": "text", "text": content}]
    else:
        content_blocks = content

    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": content_blocks,
        },
        "timestamp": timestamp,
    }


def make_tool_use_block(
    name: str,
    input_data: dict[str, Any],
    tool_use_id: str = "tool-123",
) -> dict[str, Any]:
    """
    Create a tool_use content block for assistant messages.

    Args:
        name: Tool name (e.g., "Task", "Read", "Write")
        input_data: Tool input parameters
        tool_use_id: Unique ID for the tool call

    Returns:
        Dict representing a tool_use content block
    """
    return {
        "type": "tool_use",
        "id": tool_use_id,
        "name": name,
        "input": input_data,
    }


def make_task_call(
    subagent_type: str,
    prompt: str,
    tool_use_id: str = "task-123",
) -> dict[str, Any]:
    """
    Create a Task tool call block for invoking PACT agents (legacy dispatch).

    Args:
        subagent_type: Agent type (e.g., "pact-backend-coder")
        prompt: The prompt sent to the agent
        tool_use_id: Unique ID for the tool call

    Returns:
        Dict representing a Task tool_use content block
    """
    return make_tool_use_block(
        name="Task",
        input_data={
            "subagent_type": subagent_type,
            "prompt": prompt,
            "run_in_background": True,
        },
        tool_use_id=tool_use_id,
    )


# =============================================================================
# Agent Teams Factories
# =============================================================================

def make_send_message_call(
    recipient: str,
    content: str,
    summary: str = "Status update",
    msg_type: str = "message",
    tool_use_id: str = "sendmsg-123",
) -> dict[str, Any]:
    """
    Create a SendMessage tool call block for Agent Teams communication.

    Args:
        recipient: Target teammate name (e.g., "lead", "backend-coder")
        content: Message content
        summary: Short summary for UI preview
        msg_type: Message type ("message", "shutdown_request")
        tool_use_id: Unique ID for the tool call

    Returns:
        Dict representing a SendMessage tool_use content block
    """
    return make_tool_use_block(
        name="SendMessage",
        input_data={
            "type": msg_type,
            "recipient": recipient,
            "content": content,
            "summary": summary,
        },
        tool_use_id=tool_use_id,
    )


def make_team_create_call(
    team_name: str = "pact-test1234",
    description: str = "PACT session team",
    tool_use_id: str = "teamcreate-123",
) -> dict[str, Any]:
    """
    Create a TeamCreate tool call block for Agent Teams team creation.

    Args:
        team_name: Name of the team (e.g., "pact-a1b2c3d4")
        description: Description of the team
        tool_use_id: Unique ID for the tool call

    Returns:
        Dict representing a TeamCreate tool_use content block
    """
    return make_tool_use_block(
        name="TeamCreate",
        input_data={
            "team_name": team_name,
            "description": description,
        },
        tool_use_id=tool_use_id,
    )


def make_team_task_call(
    name: str,
    team_name: str,
    subagent_type: str,
    prompt: str = "You are joining the team. Check TaskList for tasks assigned to you.",
    tool_use_id: str = "teamtask-123",
) -> dict[str, Any]:
    """
    Create a Task tool call block with team_name for Agent Teams dispatch.

    This is the Agent Teams dispatch pattern where specialists are spawned
    as teammates rather than background tasks.

    Args:
        name: Teammate name (e.g., "preparer", "backend-coder")
        team_name: Team to join (e.g., "pact-a1b2c3d4")
        subagent_type: Agent type (e.g., "pact-backend-coder")
        prompt: Thin prompt directing agent to check TaskList
        tool_use_id: Unique ID for the tool call

    Returns:
        Dict representing a Task tool_use content block with team_name
    """
    return make_tool_use_block(
        name="Task",
        input_data={
            "name": name,
            "team_name": team_name,
            "subagent_type": subagent_type,
            "prompt": prompt,
        },
        tool_use_id=tool_use_id,
    )


# =============================================================================
# Transcript Factories
# =============================================================================

def create_transcript_lines(lines: list[dict[str, Any]]) -> str:
    """
    Convert a list of message dicts to JSONL string.

    Args:
        lines: List of message dictionaries

    Returns:
        JSONL-formatted string (one JSON object per line)
    """
    return "\n".join(json.dumps(line) for line in lines)


def create_peer_review_transcript(
    step: str = "recommendations",
    include_pr_number: int | None = 64,
    include_termination: bool = False,
    include_pending_question: bool = True,
) -> str:
    """
    Generate a realistic peer-review workflow transcript.

    Args:
        step: Current workflow step (e.g., "recommendations", "merge-ready")
        include_pr_number: PR number to include in context (None to omit)
        include_termination: Whether to add termination signal
        include_pending_question: Whether to add pending AskUserQuestion

    Returns:
        JSONL string representing the transcript
    """
    lines = []
    base_time = "2025-01-22T12:00:00Z"

    # User triggers peer-review
    lines.append(make_user_message(
        "/PACT:peer-review",
        timestamp=base_time,
    ))

    # Assistant acknowledges and starts workflow
    lines.append(make_assistant_message(
        f"Starting peer-review workflow. Creating PR #{include_pr_number or 'XX'}...",
        timestamp="2025-01-22T12:00:05Z",
    ))

    # Commit phase
    lines.append(make_assistant_message(
        "Commit phase: committing changes...",
        timestamp="2025-01-22T12:00:10Z",
    ))

    # Create-PR phase
    if include_pr_number:
        lines.append(make_assistant_message(
            f"create-pr phase: PR #{include_pr_number} created successfully.",
            timestamp="2025-01-22T12:00:20Z",
        ))

    # Invoke reviewers
    lines.append(make_assistant_message(
        content=[
            {"type": "text", "text": "invoke-reviewers: Invoking review agents..."},
            make_task_call("pact-architect", "Review PR design coherence", "task-arch"),
            make_task_call("pact-test-engineer", "Review test coverage", "task-test"),
            make_task_call("pact-backend-coder", "Review implementation quality", "task-backend"),
        ],
        timestamp="2025-01-22T12:00:30Z",
    ))

    # Synthesize findings
    lines.append(make_assistant_message(
        "synthesize: All reviewers completed. No blocking issues. 0 minor, 1 future recommendation.",
        timestamp="2025-01-22T12:01:00Z",
    ))

    # Recommendations step with pending question
    if step in ["recommendations", "pre-recommendation-prompt", "merge-ready"]:
        if include_pending_question:
            lines.append(make_assistant_message(
                "recommendations phase: AskUserQuestion: Would you like to review the minor and future recommendations before merging?",
                timestamp="2025-01-22T12:01:10Z",
            ))
        else:
            lines.append(make_assistant_message(
                "recommendations phase: Presenting recommendations to user.",
                timestamp="2025-01-22T12:01:10Z",
            ))

    # Merge-ready step
    if step == "merge-ready":
        lines.append(make_assistant_message(
            "merge-ready: All checks passed. Awaiting user approval to merge.",
            timestamp="2025-01-22T12:01:30Z",
        ))

    # Termination
    if include_termination:
        lines.append(make_assistant_message(
            f"PR #{include_pr_number or 'XX'} has been merged successfully.",
            timestamp="2025-01-22T12:02:00Z",
        ))

    return create_transcript_lines(lines)


def create_orchestrate_transcript(
    phase: str = "code",
    include_task: str = "implement auth",
    include_agent_calls: bool = True,
    include_termination: bool = False,
) -> str:
    """
    Generate a realistic orchestrate workflow transcript.

    Args:
        phase: Current phase (variety-assess, prepare, architect, code, test)
        include_task: Task description
        include_agent_calls: Whether to include Task calls to PACT agents
        include_termination: Whether to add termination signal

    Returns:
        JSONL string representing the transcript
    """
    lines = []

    # User triggers orchestrate
    lines.append(make_user_message(
        f"/PACT:orchestrate {include_task}",
        timestamp="2025-01-22T10:00:00Z",
    ))

    # Variety assessment
    lines.append(make_assistant_message(
        f"variety-assess: Analyzing task: {include_task}. Estimated complexity: medium.",
        timestamp="2025-01-22T10:00:05Z",
    ))

    # Prepare phase
    if phase in ["prepare", "architect", "code", "test"]:
        content_blocks = [
            {"type": "text", "text": "prepare phase: Invoking preparer for requirements gathering."},
        ]
        if include_agent_calls:
            content_blocks.append(make_task_call("pact-preparer", "Research auth patterns", "task-prep"))
        lines.append(make_assistant_message(content_blocks, "2025-01-22T10:00:15Z"))

    # Architect phase
    if phase in ["architect", "code", "test"]:
        content_blocks = [
            {"type": "text", "text": "architect phase: Designing component structure."},
        ]
        if include_agent_calls:
            content_blocks.append(make_task_call("pact-architect", "Design auth module", "task-arch"))
        lines.append(make_assistant_message(content_blocks, "2025-01-22T10:01:00Z"))

    # Code phase
    if phase in ["code", "test"]:
        content_blocks = [
            {"type": "text", "text": "code phase: Starting implementation."},
        ]
        if include_agent_calls:
            content_blocks.append(make_task_call("pact-backend-coder", "Implement auth endpoint", "task-code"))
        lines.append(make_assistant_message(content_blocks, "2025-01-22T10:02:00Z"))

    # Test phase
    if phase == "test":
        content_blocks = [
            {"type": "text", "text": "test phase: Running comprehensive tests."},
        ]
        if include_agent_calls:
            content_blocks.append(make_task_call("pact-test-engineer", "Test auth module", "task-test"))
        lines.append(make_assistant_message(content_blocks, "2025-01-22T10:03:00Z"))

    # Termination
    if include_termination:
        lines.append(make_assistant_message(
            "all phases complete. IMPLEMENTED: Auth endpoint is ready.",
            timestamp="2025-01-22T10:05:00Z",
        ))

    return create_transcript_lines(lines)


def create_no_workflow_transcript() -> str:
    """
    Generate a transcript with no active PACT workflow.

    Returns:
        JSONL string with normal conversation (no /PACT:* triggers)
    """
    lines = [
        make_user_message("Hello, can you help me understand this codebase?"),
        make_assistant_message("Of course! Let me explore the project structure..."),
        make_user_message("What's in the hooks directory?"),
        make_assistant_message([
            {"type": "text", "text": "Looking at the hooks directory..."},
            make_tool_use_block("Read", {"file_path": "/project/hooks/hooks.json"}),
        ]),
        make_assistant_message("The hooks directory contains several Python hooks for Claude Code integration."),
    ]
    return create_transcript_lines(lines)


def create_terminated_workflow_transcript() -> str:
    """
    Generate a transcript with a completed (terminated) workflow.

    Returns:
        JSONL string with peer-review that has been merged
    """
    return create_peer_review_transcript(
        step="merge-ready",
        include_pr_number=99,
        include_termination=True,
        include_pending_question=False,
    )


def create_malformed_transcript() -> str:
    """
    Generate a transcript with malformed JSONL lines.

    Returns:
        JSONL string with some invalid lines
    """
    valid_line = make_user_message("/PACT:peer-review")
    lines = [
        json.dumps(valid_line),
        "{ invalid json",
        "",  # Empty line
        "not json at all",
        json.dumps(make_assistant_message("Starting workflow...")),
        '{"type": "unknown_type", "data": {}}',  # Unknown type
    ]
    return "\n".join(lines)


# =============================================================================
# Pytest Fixtures
# =============================================================================

@pytest.fixture
def tmp_transcript(tmp_path: Path):
    """
    Factory fixture to create temporary transcript files.

    Returns:
        Function that creates a temp JSONL file and returns its path
    """
    def _create(content: str, filename: str = "session.jsonl") -> Path:
        # Create directory structure mimicking Claude's format
        projects_dir = tmp_path / ".claude" / "projects"
        encoded_path = "-test-project"
        session_dir = projects_dir / encoded_path / "session-uuid"
        session_dir.mkdir(parents=True, exist_ok=True)

        transcript_path = session_dir / filename
        transcript_path.write_text(content, encoding="utf-8")
        return transcript_path

    return _create


@pytest.fixture
def mock_env():
    """
    Fixture to mock environment variables.

    Returns:
        Context manager function for patching environment
    """
    def _mock(project_dir: str = "/test/project"):
        return patch.dict(os.environ, {
            "CLAUDE_PROJECT_DIR": project_dir,
        })
    return _mock


@pytest.fixture
def pact_context(tmp_path, monkeypatch):
    """
    Factory fixture to create a mock PACT session context file for testing.

    Creates a temporary context file and patches _context_path
    so hooks read from it instead of the real session-scoped location.

    Usage:
        def test_something(pact_context):
            pact_context(team_name="test-team", session_id="test-session")
            # Now get_team_name() returns "test-team", etc.

    Returns:
        Function that writes a context file and returns its path
    """
    import shared.pact_context as ctx_module

    context_file = tmp_path / "pact-session-context.json"

    def _write(
        team_name="test-team",
        session_id="test-session",
        project_dir="/test/project",
        plugin_root="",
        started_at="2026-01-01T00:00:00Z",
    ):
        context_file.write_text(json.dumps({
            "team_name": team_name,
            "session_id": session_id,
            "project_dir": project_dir,
            "plugin_root": plugin_root,
            "started_at": started_at,
        }), encoding="utf-8")
        # Patch the resolved context path to point to our test file
        monkeypatch.setattr(ctx_module, "_context_path", context_file)
        # Clear the module-level cache so fresh reads happen
        monkeypatch.setattr(ctx_module, "_cache", None)
        return context_file

    # Always reset module state at fixture setup (even if _write isn't called,
    # ensures no cross-test cache or path leakage)
    monkeypatch.setattr(ctx_module, "_cache", None)
    monkeypatch.setattr(ctx_module, "_context_path", None)

    return _write


@pytest.fixture
def sample_checkpoint() -> dict[str, Any]:
    """
    Fixture providing a sample valid checkpoint.

    Returns:
        Dict representing a valid checkpoint
    """
    return {
        "version": "1.0",
        "session_id": "test-session-123",
        "workflow": {
            "name": "peer-review",
            "id": "pr-64",
            "started_at": "2025-01-22T12:00:00Z",
        },
        "step": {
            "name": "recommendations",
            "sequence": 5,
            "started_at": "2025-01-22T12:01:10Z",
        },
        "pending_action": {
            "type": "AskUserQuestion",
            "instruction": "Would you like to review the recommendations?",
            "data": {},
        },
        "context": {
            "pr_number": 64,
            "has_blocking": False,
            "minor_count": 0,
            "future_count": 1,
        },
        "extraction": {
            "confidence": 0.9,
            "notes": "clear trigger, step: recommendations, 3 agent call(s)",
            "transcript_lines_scanned": 150,
        },
        "created_at": "2025-01-22T12:05:30Z",
    }


# =============================================================================
# Fixture Files
# =============================================================================

@pytest.fixture
def peer_review_mid_workflow_transcript() -> str:
    """Fixture returning a peer-review transcript mid-workflow."""
    return create_peer_review_transcript(
        step="recommendations",
        include_pr_number=64,
        include_termination=False,
        include_pending_question=True,
    )


@pytest.fixture
def orchestrate_code_phase_transcript() -> str:
    """Fixture returning an orchestrate transcript in CODE phase."""
    return create_orchestrate_transcript(
        phase="code",
        include_task="implement auth endpoint",
        include_agent_calls=True,
        include_termination=False,
    )


@pytest.fixture
def no_workflow_transcript() -> str:
    """Fixture returning a transcript with no active workflow."""
    return create_no_workflow_transcript()


@pytest.fixture
def terminated_workflow_transcript() -> str:
    """Fixture returning a transcript with completed workflow."""
    return create_terminated_workflow_transcript()


# =============================================================================
# CLI Test Factories
# =============================================================================

@pytest.fixture
def cli_memory_dict():
    """
    Factory fixture wrapping make_cli_memory_dict from helpers.py.

    Returns a function that creates a minimal memory dict suitable for
    CLI save command tests. Mirrors helpers.make_cli_memory_dict().

    Usage:
        def test_something(cli_memory_dict):
            memory = cli_memory_dict(context="my context")
    """
    from helpers import make_cli_memory_dict
    return make_cli_memory_dict
