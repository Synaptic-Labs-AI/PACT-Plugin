"""
Location: pact-plugin/hooks/refresh/workflow_detector.py
Summary: Detect active PACT workflow from parsed transcript turns.
Used by: refresh/__init__.py for workflow state extraction.

Scans transcript turns to identify the most recent active workflow,
checking for trigger commands, agent/teammate invocations, and termination
signals. Supports both v2 subagent model and v3 Agent Teams model.
"""

from dataclasses import dataclass

from .transcript_parser import Turn, find_trigger_turn_index
from .patterns import (
    WORKFLOW_PATTERNS,
    TRIGGER_PATTERNS,
    is_termination_signal,
    extract_context_value,
    CONFIDENCE_WEIGHTS,
    TERMINATION_WINDOW_TURNS,
)


@dataclass
class WorkflowInfo:
    """
    Information about a detected workflow.

    Attributes:
        name: Workflow name (e.g., "peer-review", "orchestrate")
        workflow_id: Optional identifier (e.g., "pr-64")
        started_at: Timestamp when workflow was triggered
        trigger_turn: The Turn that triggered this workflow
        confidence: Detection confidence score (0.0 to 1.0)
        is_terminated: Whether workflow appears to have completed
        notes: Human-readable notes about detection
    """

    name: str
    workflow_id: str = ""
    started_at: str = ""
    trigger_turn: Turn | None = None
    confidence: float = 0.0
    is_terminated: bool = False
    notes: str = ""


def find_workflow_trigger(turns: list[Turn]) -> tuple[str, Turn | None]:
    """
    Find the most recent workflow trigger in the turns.

    Scans backwards through user messages looking for /PACT:* commands.

    Args:
        turns: List of turns in chronological order

    Returns:
        Tuple of (workflow_name, trigger_turn) or ("", None) if not found
    """
    # Scan backwards for most recent trigger
    for turn in reversed(turns):
        if not turn.is_user:
            continue

        content = turn.content
        for workflow_name, pattern in TRIGGER_PATTERNS.items():
            if pattern.search(content):
                return workflow_name, turn

    return "", None


def check_workflow_termination(
    turns: list[Turn],
    workflow_name: str,
    trigger_index: int,
) -> bool:
    """
    Check if the workflow has terminated since its trigger.

    Scans the last N turns after the trigger for termination signals.
    (Fix 8: Only check recent turns to avoid false positives from earlier
    workflow completions in the transcript.)

    Args:
        turns: List of turns in chronological order
        workflow_name: Name of the workflow to check
        trigger_index: Index of the trigger turn in the list

    Returns:
        True if workflow appears terminated
    """
    # Only check the last TERMINATION_WINDOW_TURNS turns after trigger
    # This prevents false positives from old termination signals
    relevant_turns = turns[trigger_index + 1:]
    if len(relevant_turns) > TERMINATION_WINDOW_TURNS:
        relevant_turns = relevant_turns[-TERMINATION_WINDOW_TURNS:]

    for turn in relevant_turns:
        if turn.is_assistant:
            if is_termination_signal(turn.content, workflow_name):
                return True
    return False


def find_workflow_id(turns: list[Turn], workflow_name: str) -> str:
    """
    Extract a workflow-specific ID if available.

    For peer-review, extracts PR number. For orchestrate, extracts
    task identifier if present.

    Args:
        turns: List of turns to search
        workflow_name: Name of the workflow

    Returns:
        Workflow ID string or empty string
    """
    if workflow_name == "peer-review":
        # Look for PR number in any turn
        for turn in turns:
            pr_num = extract_context_value(turn.content, "pr_number")
            if pr_num:
                return f"pr-{pr_num}"

    # Could add more workflow-specific ID extraction here
    return ""


def count_pact_agent_calls(turns: list[Turn], after_index: int = 0) -> int:
    """
    Count Task calls to PACT agents/teammates after a given index.

    Counts both v2 subagent Task calls and v3 Agent Teams Task calls
    (both use subagent_type with "pact-" prefix).

    Args:
        turns: List of turns
        after_index: Only count calls after this index

    Returns:
        Number of PACT agent/teammate invocations
    """
    count = 0
    for turn in turns[after_index:]:
        if turn.has_task_to_pact_agent():
            count += 1
    return count


def count_team_interactions(turns: list[Turn], after_index: int = 0) -> int:
    """
    Count Agent Teams interactions (SendMessage, TeamCreate) after a given index.

    This provides additional signal strength for v3 Agent Teams workflows.

    Args:
        turns: List of turns
        after_index: Only count calls after this index

    Returns:
        Number of team interaction calls
    """
    count = 0
    for turn in turns[after_index:]:
        if turn.has_send_message() or turn.has_team_create():
            count += 1
    return count


def calculate_detection_confidence(
    workflow_name: str,
    trigger_turn: Turn | None,
    turns: list[Turn],
    trigger_index: int,
) -> tuple[float, str]:
    """
    Calculate confidence score for workflow detection.

    Combines multiple signals:
    - Clear trigger command (0.4)
    - Step markers in content (0.2)
    - PACT agent invocations (0.2)
    - Pending action indicators (0.1)
    - Context richness (0.1)

    Args:
        workflow_name: Detected workflow name
        trigger_turn: The turn that triggered the workflow
        turns: All turns
        trigger_index: Index of trigger in turns

    Returns:
        Tuple of (confidence_score, notes_string)
    """
    confidence = 0.0
    notes_parts = []

    # Clear trigger command
    if trigger_turn:
        confidence += CONFIDENCE_WEIGHTS["clear_trigger"]
        notes_parts.append("clear trigger")

    # Check for step markers in recent turns
    pattern = WORKFLOW_PATTERNS.get(workflow_name)
    if pattern:
        step_markers = pattern.step_markers
        for turn in turns[trigger_index:]:
            if turn.is_assistant:
                content_lower = turn.content.lower()
                for marker in step_markers:
                    if marker.lower() in content_lower:
                        confidence += CONFIDENCE_WEIGHTS["step_marker"]
                        notes_parts.append(f"step: {marker}")
                        break  # Only count once per turn
                break  # Only check first assistant turn after trigger

    # Check for PACT agent/teammate invocations
    agent_calls = count_pact_agent_calls(turns, trigger_index)
    team_interactions = count_team_interactions(turns, trigger_index)
    total_dispatches = agent_calls + team_interactions
    if total_dispatches > 0:
        confidence += CONFIDENCE_WEIGHTS["agent_invocation"]
        if team_interactions > 0:
            notes_parts.append(f"{agent_calls} agent call(s), {team_interactions} team interaction(s)")
        else:
            notes_parts.append(f"{agent_calls} agent call(s)")

    # Check for pending action indicators
    from .patterns import PENDING_ACTION_PATTERNS
    for turn in reversed(turns[trigger_index:]):
        if turn.is_assistant:
            for action_type, action_pattern in PENDING_ACTION_PATTERNS.items():
                if action_pattern.search(turn.content):
                    confidence += CONFIDENCE_WEIGHTS["pending_action"]
                    notes_parts.append(f"pending: {action_type}")
                    break
            break  # Only check last assistant turn

    # Check context richness
    has_context = False
    for turn in turns[trigger_index:]:
        if extract_context_value(turn.content, "pr_number"):
            has_context = True
            break
        if extract_context_value(turn.content, "task_summary"):
            has_context = True
            break

    if has_context:
        confidence += CONFIDENCE_WEIGHTS["context_richness"]
        notes_parts.append("rich context")

    # Cap at 1.0
    confidence = min(confidence, 1.0)

    notes = ", ".join(notes_parts) if notes_parts else "weak signals"
    return confidence, notes


def detect_active_workflow(turns: list[Turn]) -> WorkflowInfo | None:
    """
    Detect the currently active workflow from transcript turns.

    This is the main entry point for workflow detection. It:
    1. Scans backwards for the most recent /PACT:* trigger
    2. Checks if that workflow has terminated
    3. Calculates detection confidence
    4. Returns WorkflowInfo if an active workflow is found

    Args:
        turns: List of Turn objects in chronological order

    Returns:
        WorkflowInfo if an active workflow is detected, None otherwise
    """
    if not turns:
        return None

    # Find most recent workflow trigger
    workflow_name, trigger_turn = find_workflow_trigger(turns)

    if not workflow_name or not trigger_turn:
        return None

    # Find trigger index for subsequent checks (Fix 4: use shared utility)
    trigger_index = find_trigger_turn_index(turns, trigger_turn.line_number)

    # Check if workflow has terminated
    is_terminated = check_workflow_termination(turns, workflow_name, trigger_index)

    if is_terminated:
        return WorkflowInfo(
            name=workflow_name,
            workflow_id=find_workflow_id(turns, workflow_name),
            started_at=trigger_turn.timestamp,
            trigger_turn=trigger_turn,
            confidence=0.9,  # High confidence since we found both trigger and termination
            is_terminated=True,
            notes="Workflow completed",
        )

    # Calculate confidence for active workflow
    confidence, notes = calculate_detection_confidence(
        workflow_name, trigger_turn, turns, trigger_index
    )

    return WorkflowInfo(
        name=workflow_name,
        workflow_id=find_workflow_id(turns, workflow_name),
        started_at=trigger_turn.timestamp,
        trigger_turn=trigger_turn,
        confidence=confidence,
        is_terminated=False,
        notes=notes,
    )
