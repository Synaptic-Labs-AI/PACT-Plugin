"""
Shared test fixtures for the refresh system tests.

Provides factories for generating realistic JSONL transcripts and
common fixtures used across multiple test modules.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


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
        msg_type: Message type ("message", "broadcast", "shutdown_request")
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


def create_plan_mode_transcript(
    step: str = "consult",
    include_task: str = "implement new feature",
    include_termination: bool = False,
) -> str:
    """
    Generate a realistic plan-mode workflow transcript.

    Args:
        step: Current workflow step (analyze, consult, synthesize, present)
        include_task: Task description
        include_termination: Whether to add termination signal

    Returns:
        JSONL string representing the transcript
    """
    lines = []

    # User triggers plan-mode
    lines.append(make_user_message(
        f"/PACT:plan-mode {include_task}",
        timestamp="2025-01-22T09:00:00Z",
    ))

    # Analyze phase
    lines.append(make_assistant_message(
        f"analyze: Assessing scope for: {include_task}. Determining specialists needed.",
        timestamp="2025-01-22T09:00:05Z",
    ))

    # Consult phase - invoke specialists for planning perspectives
    if step in ["consult", "synthesize", "present"]:
        lines.append(make_assistant_message(
            content=[
                {"type": "text", "text": "consult: Invoking specialists for planning perspectives..."},
                make_task_call("pact-architect", "Provide architectural perspective for plan", "task-arch-plan"),
                make_task_call("pact-backend-coder", "Provide implementation perspective", "task-backend-plan"),
            ],
            timestamp="2025-01-22T09:00:15Z",
        ))

    # Synthesize phase
    if step in ["synthesize", "present"]:
        lines.append(make_assistant_message(
            "synthesize: All specialists responded. Resolving conflicts and sequencing work.",
            timestamp="2025-01-22T09:01:00Z",
        ))

    # Present phase
    if step == "present":
        lines.append(make_assistant_message(
            "present: Plan ready. AskUserQuestion: Would you like to review the plan before approval?",
            timestamp="2025-01-22T09:01:30Z",
        ))

    # Termination
    if include_termination:
        lines.append(make_assistant_message(
            "Plan saved to docs/plans/new-feature-plan.md. Awaiting approval to proceed.",
            timestamp="2025-01-22T09:02:00Z",
        ))

    return create_transcript_lines(lines)


def create_compact_transcript(
    specialist: str = "backend",
    include_task: str = "fix auth bug",
    include_termination: bool = False,
) -> str:
    """
    Generate a realistic comPACT workflow transcript.

    Args:
        specialist: Specialist type (backend, frontend, database, test, architect)
        include_task: Task description
        include_termination: Whether to add termination signal

    Returns:
        JSONL string representing the transcript
    """
    lines = []

    # User triggers comPACT
    lines.append(make_user_message(
        f"/PACT:comPACT {specialist} {include_task}",
        timestamp="2025-01-22T14:00:00Z",
    ))

    # Specialist selection
    lines.append(make_assistant_message(
        f"invoking-specialist: Delegating to pact-{specialist}-coder with light ceremony.",
        timestamp="2025-01-22T14:00:05Z",
    ))

    # Invoke specialist
    agent_type = f"pact-{specialist}-coder" if specialist not in ["test", "architect"] else f"pact-{specialist}"
    if specialist == "test":
        agent_type = "pact-test-engineer"

    lines.append(make_assistant_message(
        content=[
            {"type": "text", "text": f"Invoking {agent_type} for: {include_task}"},
            make_task_call(agent_type, f"Execute: {include_task}", "task-compact"),
        ],
        timestamp="2025-01-22T14:00:10Z",
    ))

    # Termination
    if include_termination:
        lines.append(make_assistant_message(
            f"specialist completed: Task complete. Handoff complete.",
            timestamp="2025-01-22T14:05:00Z",
        ))

    return create_transcript_lines(lines)


def create_agent_teams_orchestrate_transcript(
    phase: str = "code",
    include_task: str = "implement auth",
    include_termination: bool = False,
    team_name: str = "pact-test1234",
) -> str:
    """
    Generate a realistic orchestrate workflow transcript using Agent Teams dispatch.

    Uses TeamCreate + Task(team_name=...) dispatch pattern instead of the legacy
    Task(subagent_type=..., run_in_background=True) pattern.

    Args:
        phase: Current phase (variety-assess, prepare, architect, code, test)
        include_task: Task description
        include_termination: Whether to add termination signal
        team_name: Session-unique team name for Agent Teams dispatch (e.g., "pact-a1b2c3d4")

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

    # Team creation
    lines.append(make_assistant_message(
        content=[
            {"type": "text", "text": f"Creating team {team_name} for this session."},
            make_team_create_call(team_name=team_name, tool_use_id="teamcreate-orch"),
        ],
        timestamp="2025-01-22T10:00:08Z",
    ))

    # Prepare phase
    if phase in ["prepare", "architect", "code", "test"]:
        lines.append(make_assistant_message(
            content=[
                {"type": "text", "text": "prepare phase: Spawning preparer as teammate."},
                make_team_task_call(
                    name="preparer",
                    team_name=team_name,
                    subagent_type="pact-preparer",
                    tool_use_id="teamtask-prep",
                ),
            ],
            timestamp="2025-01-22T10:00:15Z",
        ))

    # Architect phase
    if phase in ["architect", "code", "test"]:
        lines.append(make_assistant_message(
            content=[
                {"type": "text", "text": "architect phase: Spawning architect as teammate."},
                make_team_task_call(
                    name="architect",
                    team_name=team_name,
                    subagent_type="pact-architect",
                    tool_use_id="teamtask-arch",
                ),
            ],
            timestamp="2025-01-22T10:01:00Z",
        ))

    # Code phase
    if phase in ["code", "test"]:
        lines.append(make_assistant_message(
            content=[
                {"type": "text", "text": "code phase: Spawning backend coder as teammate."},
                make_team_task_call(
                    name="backend-coder",
                    team_name=team_name,
                    subagent_type="pact-backend-coder",
                    tool_use_id="teamtask-code",
                ),
            ],
            timestamp="2025-01-22T10:02:00Z",
        ))

    # Completion summary from coder (metadata-first HANDOFF)
    if phase in ["code", "test"]:
        lines.append(make_assistant_message(
            content=[
                {"type": "text", "text": "Received completion summary from backend-coder. Full HANDOFF stored in task metadata."},
                make_send_message_call(
                    recipient="lead",
                    content="Task complete. Implemented auth endpoint using JWT tokens. No HIGH uncertainties.",
                    summary="Task complete: auth endpoint",
                    tool_use_id="sendmsg-handoff",
                ),
            ],
            timestamp="2025-01-22T10:02:30Z",
        ))

    # Test phase
    if phase == "test":
        lines.append(make_assistant_message(
            content=[
                {"type": "text", "text": "test phase: Spawning test engineer as teammate."},
                make_team_task_call(
                    name="test-engineer",
                    team_name=team_name,
                    subagent_type="pact-test-engineer",
                    tool_use_id="teamtask-test",
                ),
            ],
            timestamp="2025-01-22T10:03:00Z",
        ))

    # Termination
    if include_termination:
        lines.append(make_assistant_message(
            "all phases complete. IMPLEMENTED: Auth endpoint is ready.",
            timestamp="2025-01-22T10:05:00Z",
        ))

    return create_transcript_lines(lines)


def create_repact_transcript(
    nested_phase: str = "nested-code",
    parent_workflow: str = "orchestrate",
    include_termination: bool = False,
) -> str:
    """
    Generate a realistic rePACT (nested PACT) workflow transcript.

    Args:
        nested_phase: Current nested phase (nested-prepare, nested-architect, nested-code, nested-test)
        parent_workflow: Parent workflow that spawned rePACT
        include_termination: Whether to add termination signal

    Returns:
        JSONL string representing the transcript
    """
    lines = []

    # Parent workflow context (orchestrate in code phase discovers need for nested cycle)
    lines.append(make_user_message(
        "/PACT:orchestrate implement complex feature",
        timestamp="2025-01-22T10:00:00Z",
    ))

    lines.append(make_assistant_message(
        "code phase: Complexity detected. Invoking rePACT for sub-component.",
        timestamp="2025-01-22T10:30:00Z",
    ))

    # User triggers rePACT (or orchestrator invokes it)
    lines.append(make_user_message(
        "/PACT:rePACT implement auth sub-module",
        timestamp="2025-01-22T10:30:05Z",
    ))

    # Nested prepare
    lines.append(make_assistant_message(
        content=[
            {"type": "text", "text": "nested-prepare: Starting nested PACT cycle for sub-module."},
            make_task_call("pact-preparer", "Research auth sub-module requirements", "task-nested-prep"),
        ],
        timestamp="2025-01-22T10:30:10Z",
    ))

    # Nested architect
    if nested_phase in ["nested-architect", "nested-code", "nested-test"]:
        lines.append(make_assistant_message(
            content=[
                {"type": "text", "text": "nested-architect: Designing sub-module architecture."},
                make_task_call("pact-architect", "Design auth sub-module", "task-nested-arch"),
            ],
            timestamp="2025-01-22T10:31:00Z",
        ))

    # Nested code
    if nested_phase in ["nested-code", "nested-test"]:
        lines.append(make_assistant_message(
            content=[
                {"type": "text", "text": "nested-code: Implementing sub-module."},
                make_task_call("pact-backend-coder", "Implement auth sub-module", "task-nested-code"),
            ],
            timestamp="2025-01-22T10:32:00Z",
        ))

    # Nested test
    if nested_phase == "nested-test":
        lines.append(make_assistant_message(
            content=[
                {"type": "text", "text": "nested-test: Testing sub-module."},
                make_task_call("pact-test-engineer", "Test auth sub-module", "task-nested-test"),
            ],
            timestamp="2025-01-22T10:33:00Z",
        ))

    # Termination
    if include_termination:
        lines.append(make_assistant_message(
            "nested cycle complete. rePACT complete. Returning to parent workflow.",
            timestamp="2025-01-22T10:35:00Z",
        ))

    return create_transcript_lines(lines)


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
    def _mock(session_id: str = "test-session-123", project_dir: str = "/test/project"):
        return patch.dict(os.environ, {
            "CLAUDE_SESSION_ID": session_id,
            "CLAUDE_PROJECT_DIR": project_dir,
        })
    return _mock


@pytest.fixture
def tmp_checkpoint_dir(tmp_path: Path):
    """
    Fixture providing a temporary checkpoint directory.

    Returns:
        Path to the temporary checkpoint directory
    """
    checkpoint_dir = tmp_path / ".claude" / "pact-refresh"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    return checkpoint_dir


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


@pytest.fixture
def plan_mode_mid_workflow_transcript() -> str:
    """Fixture returning a plan-mode transcript mid-workflow (in consult phase)."""
    return create_plan_mode_transcript(
        step="consult",
        include_task="implement new feature",
        include_termination=False,
    )


@pytest.fixture
def compact_mid_workflow_transcript() -> str:
    """Fixture returning a comPACT transcript mid-workflow."""
    return create_compact_transcript(
        specialist="backend",
        include_task="fix auth bug",
        include_termination=False,
    )


@pytest.fixture
def repact_mid_workflow_transcript() -> str:
    """Fixture returning a rePACT transcript mid-workflow (in nested-code phase)."""
    return create_repact_transcript(
        nested_phase="nested-code",
        parent_workflow="orchestrate",
        include_termination=False,
    )


@pytest.fixture
def agent_teams_orchestrate_transcript() -> str:
    """Fixture returning an Agent Teams orchestrate transcript in CODE phase."""
    return create_agent_teams_orchestrate_transcript(
        phase="code",
        include_task="implement auth endpoint",
        include_termination=False,
    )
