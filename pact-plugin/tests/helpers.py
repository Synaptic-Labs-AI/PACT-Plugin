"""
Test helper functions for pact-plugin tests.

Location: pact-plugin/tests/helpers.py
Purpose: Provides shared utilities and factory functions used across test modules.
         These are importable utilities, separate from pytest fixtures in conftest.py.

Usage:
    from helpers import parse_frontmatter, create_test_schema
    from helpers import create_peer_review_transcript, create_terminated_workflow_transcript
"""

import json
from datetime import datetime, timezone
from typing import Any


# =============================================================================
# Shared Utilities: Frontmatter Parser
# =============================================================================

def parse_frontmatter(text):
    """Parse YAML frontmatter from markdown text.

    Handles simple key: value pairs and multiline values using the | block
    scalar indicator. Used by agent, command, and skill structure tests.
    """
    if not text.startswith("---"):
        return None
    end = text.index("---", 3)
    fm_text = text[3:end].strip()
    result = {}
    current_key = None
    for line in fm_text.split("\n"):
        if line.startswith("  ") and current_key:
            # Continuation of multiline value
            if current_key not in result:
                result[current_key] = ""
            result[current_key] += line.strip() + " "
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "|":
                current_key = key
                result[key] = ""
            else:
                result[key] = value
                current_key = key
    return result


# =============================================================================
# Shared Utilities: Memory Database Schema
# =============================================================================

def create_test_schema(conn):
    """Create the pact-memory database schema for testing.

    Creates memories, files, memory_files, and file_relations tables with
    all indexes. Bypasses pysqlite3 compatibility issues by using raw SQL.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            context TEXT, goal TEXT,
            active_tasks TEXT, lessons_learned TEXT,
            decisions TEXT, entities TEXT,
            reasoning_chains TEXT, agreements_reached TEXT,
            disagreements_resolved TEXT,
            project_id TEXT, session_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL, project_id TEXT,
            last_modified TEXT,
            UNIQUE(path, project_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_files (
            memory_id TEXT REFERENCES memories(id) ON DELETE CASCADE,
            file_id TEXT REFERENCES files(id),
            relationship TEXT DEFAULT 'modified',
            PRIMARY KEY (memory_id, file_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_relations (
            source_file TEXT REFERENCES files(id),
            target_file TEXT REFERENCES files(id),
            relationship TEXT,
            PRIMARY KEY (source_file, target_file, relationship)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_files_file ON memory_files(file_id)")
    conn.commit()


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


# =============================================================================
# CLI Test Factories
# =============================================================================

def make_cli_memory_dict(
    context: str = "Working on CLI tests",
    goal: str = "Verify pact-memory CLI entry point",
    lessons_learned: list[str] | None = None,
    decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Create a minimal memory dict suitable for CLI save command tests.

    Returns a dict that json.dumps() can serialize and PACTMemory.save()
    can accept. Keeps only the fields commonly used in CLI testing.

    Args:
        context: Context description for the memory.
        goal: Goal description for the memory.
        lessons_learned: Optional list of lesson strings.
        decisions: Optional list of decision dicts.

    Returns:
        Dict suitable for CLI save command JSON input.
    """
    result: dict[str, Any] = {
        "context": context,
        "goal": goal,
    }
    if lessons_learned is not None:
        result["lessons_learned"] = lessons_learned
    if decisions is not None:
        result["decisions"] = decisions
    return result


# =============================================================================
# Pin Caps Fixture Factories (#492)
# =============================================================================

def make_pin_entry(
    title: str = "Entry",
    body_chars: int = 100,
    date: str = "2026-04-20",
    override_rationale: str | None = None,
    stale_date: str | None = None,
) -> str:
    """Build a single pinned entry with optional override and stale marker.

    Produces the two-line comment + heading + body shape expected by
    pin_caps.parse_pins. Body is padded with 'x' characters to reach the
    requested body_chars count (post-marker-stripping). The override
    rationale, when provided, is inserted into the `<!-- pinned: ... -->`
    annotation line per the plan's combined-comment grammar.
    """
    if override_rationale is not None:
        comment = f"<!-- pinned: {date}, pin-size-override: {override_rationale} -->"
    else:
        comment = f"<!-- pinned: {date} -->"

    lines = [comment, f"### {title}"]
    if stale_date is not None:
        lines.append(f"<!-- STALE: Last relevant {stale_date} -->")

    # Body is plain text of exactly body_chars chars (post-strip). Use 'x'
    # fill since markers are stripped before counting.
    if body_chars > 0:
        lines.append("x" * body_chars)

    return "\n".join(lines)


def make_pinned_section(entries: list[str]) -> str:
    """Join pinned entries into a Pinned Context section body.

    Returned text is suitable for pin_caps.parse_pins (i.e., AFTER the
    "## Pinned Context" heading — the third element of
    staleness._parse_pinned_section).
    """
    return "\n\n".join(entries) + "\n"


def make_claude_md_with_pins(entries: list[str]) -> str:
    """Build a full-file CLAUDE.md with managed-region wrapping + pins.

    Matches the layered boundary contract (#404): outer PACT_MANAGED_START/END
    wraps all plugin-managed content. Pinned Context lives inside the
    managed region so pin_caps.parse_pins + staleness._parse_pinned_section
    both read it via _extract_managed_region.
    """
    pinned_body = make_pinned_section(entries)
    return (
        "# PACT Framework and Managed Project Memory\n"
        "\n"
        "<!-- PACT_MANAGED_START -->\n"
        "<!-- PACT_MEMORY_START -->\n"
        "## Pinned Context\n"
        "\n"
        f"{pinned_body}"
        "## Working Memory\n"
        "<!-- PACT_MEMORY_END -->\n"
        "<!-- PACT_MANAGED_END -->\n"
    )
