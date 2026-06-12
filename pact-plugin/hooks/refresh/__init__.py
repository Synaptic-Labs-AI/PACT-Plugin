"""
Location: pact-plugin/hooks/refresh/__init__.py
Summary: Package for extracting workflow state from JSONL transcripts.
Used by: PreCompact hook to capture state before context compaction.

This package provides transcript parsing and workflow state extraction
for the compaction refresh system. The main entry point is
`extract_workflow_state()` which returns a checkpoint-ready dict.
"""

from __future__ import annotations

from .transcript_parser import parse_transcript, Turn
from .workflow_detector import detect_active_workflow, WorkflowInfo
from .step_extractor import extract_current_step, StepInfo, PendingAction
from .checkpoint_builder import build_checkpoint, get_checkpoint_path, CheckpointSchema
from .patterns import WORKFLOW_PATTERNS, CONFIDENCE_THRESHOLD

__all__ = [
    # Main entry point
    "extract_workflow_state",
    # Parser
    "parse_transcript",
    "Turn",
    # Workflow detection
    "detect_active_workflow",
    "WorkflowInfo",
    # Step extraction
    "extract_current_step",
    "StepInfo",
    "PendingAction",
    # Checkpoint building
    "build_checkpoint",
    "get_checkpoint_path",
    "CheckpointSchema",
    # Patterns and constants
    "WORKFLOW_PATTERNS",
    "CONFIDENCE_THRESHOLD",
]


def extract_workflow_state(transcript_path: str) -> dict | None:
    """
    Extract workflow state from a JSONL transcript file.

    Main entry point for the refresh system. Parses the transcript,
    detects any active workflow, extracts the current step and pending
    action, and builds a checkpoint dict suitable for refresh.

    Args:
        transcript_path: Absolute path to the JSONL transcript file

    Returns:
        Checkpoint dict if an active workflow is detected with confidence >= 0.3,
        None otherwise. The dict follows the checkpoint schema defined in
        the refresh plan.
    """
    from pathlib import Path

    path = Path(transcript_path)
    if not path.exists():
        return None

    # Parse transcript (last 500 lines, scanning backwards)
    turns = parse_transcript(path, max_lines=500)
    if not turns:
        return None

    # Detect active workflow
    workflow_info = detect_active_workflow(turns)
    if workflow_info is None:
        return None

    # Extract current step and pending action
    step_info = extract_current_step(turns, workflow_info)

    # Build checkpoint
    checkpoint = build_checkpoint(
        transcript_path=transcript_path,
        workflow_info=workflow_info,
        step_info=step_info,
        lines_scanned=len(turns),
    )

    # Only return if confidence meets threshold (Fix 3: use named constant)
    if checkpoint and checkpoint.get("extraction", {}).get("confidence", 0) >= CONFIDENCE_THRESHOLD:
        return checkpoint

    return None
