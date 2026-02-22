"""
Location: pact-plugin/hooks/refresh/patterns.py
Summary: Workflow detection patterns and signals for transcript parsing.
Used by: workflow_detector.py and step_extractor.py for pattern matching.

Defines the trigger patterns, step markers, and termination signals
for each PACT workflow type as specified in the refresh plan.
Configuration constants are imported from constants.py for maintainability.

Supports both dispatch models for backward compatibility:
- Background Task agent: Task(subagent_type="pact-*", run_in_background=true)
- Agent Teams teammate: Task(name="pact-*", team_name="pact-{session_hash}", subagent_type="pact-*")
"""

import re
from dataclasses import dataclass

# Import configuration constants from centralized location (Item 12)
from .constants import (
    CONFIDENCE_THRESHOLD,
    CONFIDENCE_AUTO_PROCEED_THRESHOLD,
    CONFIDENCE_LABEL_MEDIUM,
    PENDING_ACTION_INSTRUCTION_MAX_LENGTH,
    REVIEW_PROMPT_INSTRUCTION_MAX_LENGTH,
    TASK_SUMMARY_MAX_LENGTH,
    TERMINATION_WINDOW_TURNS,
)

# Re-export for backwards compatibility
__all__ = [
    "CONFIDENCE_THRESHOLD",
    "CONFIDENCE_AUTO_PROCEED_THRESHOLD",
    "CONFIDENCE_LABEL_MEDIUM",
    "PENDING_ACTION_INSTRUCTION_MAX_LENGTH",
    "REVIEW_PROMPT_INSTRUCTION_MAX_LENGTH",
    "TASK_SUMMARY_MAX_LENGTH",
    "TERMINATION_WINDOW_TURNS",
    "WorkflowPattern",
    "TRIGGER_PATTERNS",
    "STEP_MARKERS",
    "TERMINATION_SIGNALS",
    "PACT_AGENT_PATTERN",
    "TASK_TOOL_PATTERN",
    "SUBAGENT_TYPE_PATTERN",
    "CONTEXT_EXTRACTORS",
    "PENDING_ACTION_PATTERNS",
    "CONFIDENCE_WEIGHTS",
    "compile_workflow_patterns",
    "WORKFLOW_PATTERNS",
    "is_termination_signal",
    "extract_context_value",
]


@dataclass
class WorkflowPattern:
    """Pattern definition for a single PACT workflow type."""

    name: str
    trigger_pattern: re.Pattern[str]
    step_markers: list[str]
    termination_signals: list[str]
    # Optional: patterns for extracting workflow-specific context
    context_extractors: dict[str, re.Pattern[str]]


# Workflow trigger patterns (match user messages that start workflows)
TRIGGER_PATTERNS = {
    "peer-review": re.compile(r"/PACT:peer-review", re.IGNORECASE),
    "orchestrate": re.compile(r"/PACT:orchestrate", re.IGNORECASE),
    "plan-mode": re.compile(r"/PACT:plan-mode", re.IGNORECASE),
    "comPACT": re.compile(r"/PACT:comPACT", re.IGNORECASE),
    "rePACT": re.compile(r"/PACT:rePACT", re.IGNORECASE),
    "imPACT": re.compile(r"/PACT:imPACT", re.IGNORECASE),
}

# Step markers for each workflow (appear in assistant messages)
STEP_MARKERS = {
    "peer-review": [
        "commit",
        "create-pr",
        "invoke-reviewers",
        "synthesize",
        "recommendations",
        "pre-recommendation-prompt",
        "merge-ready",
        "awaiting-merge",
    ],
    "orchestrate": [
        "variety-assess",
        "prepare",
        "architect",
        "code",
        "test",
        "peer-review",
    ],
    "plan-mode": [
        "analyze",
        "consult",
        "synthesize",
        "present",
    ],
    "comPACT": [
        "invoking-specialist",
        "specialist-completed",
    ],
    "rePACT": [
        "nested-prepare",
        "nested-architect",
        "nested-code",
        "nested-test",
    ],
    "imPACT": [
        "triage",
        "assessing-redo",
        "selecting-agents",
        "resolution-path",
    ],
}

# Termination signals (indicate workflow has completed)
TERMINATION_SIGNALS = {
    "peer-review": [
        r"(?:PR|pull request)\s+(?:has been\s+)?merged",
        r"PR\s+closed",
        r"user\s+declined",
        r"merge\s+complete",
        r"successfully\s+merged",
    ],
    "orchestrate": [
        r"all\s+phases?\s+complete",
        r"IMPLEMENTED",
        r"workflow\s+complete",
        r"orchestration\s+complete",
    ],
    "plan-mode": [
        r"plan\s+saved",
        r"plan\s+presented",
        r"awaiting\s+approval",
        r"plan\s+complete",
    ],
    "comPACT": [
        r"specialist\s+completed",
        r"task\s+complete",
        r"handoff\s+complete",
    ],
    "rePACT": [
        r"nested\s+cycle\s+complete",
        r"rePACT\s+complete",
    ],
    "imPACT": [
        # v3.5.0 outcome names (authoritative, from imPACT.md)
        # Anchored with (?:^|[:.>\-]\s*) to avoid matching mid-sentence
        # triage discussion (e.g., "Assessing whether to redo prior phase")
        r"(?:^|[:.>\-]\s*)redo\s+prior\s+phase",
        r"(?:^|[:.>\-]\s*)augment\s+present\s+phase",
        r"(?:^|[:.>\-]\s*)invoke\s+rePACT",
        r"(?:^|[:.>\-]\s*)terminate\s+agent",
        r"(?:^|[:.>\-]\s*)not\s+truly\s+blocked",
        r"(?:^|[:.>\-]\s*)escalate\s+to\s+user",
        # v3.4 outcome names (kept for backwards compatibility with old transcripts)
        r"redo\s+solo",
        r"redo\s+with\s+help",
        r"proceed\s+with\s+help",
        r"imPACT\s+resolved",
        r"returning\s+to\s+(?:main\s+)?workflow",
        r"blocker\s+resolved",
    ],
}

# Agent type patterns (for detecting Task tool calls to PACT agents)
PACT_AGENT_PATTERN = re.compile(r"\bpact-(preparer|architect|backend-coder|frontend-coder|database-engineer|devops-engineer|n8n|test-engineer|security-engineer|qa-engineer|memory-agent)(?![\w-])")

# Tool call patterns - support both dispatch models:
# - Background Task agent: Task(subagent_type="pact-*", run_in_background=true)
# - Agent Teams teammate: Task(name="pact-*", team_name="pact-{session_hash}", subagent_type="pact-*")
#   where team_name is session-unique (e.g., "pact-0001639f")
# Both include subagent_type, so SUBAGENT_TYPE_PATTERN matches either model.
TASK_TOOL_PATTERN = re.compile(r'"name":\s*"Task"', re.IGNORECASE)
SUBAGENT_TYPE_PATTERN = re.compile(r'"subagent_type":\s*"([^"]+)"')

# Context extraction patterns (for building rich checkpoint context)
CONTEXT_EXTRACTORS = {
    "pr_number": re.compile(r"(?:PR|pull request)\s*#?(\d+)", re.IGNORECASE),
    "branch_name": re.compile(r"(?:branch|feature)[:\s]+([a-zA-Z0-9_/-]+)"),
    "task_summary": re.compile(r"(?:task|implementing|working on)[:\s]+(.{10,100})", re.IGNORECASE),
}

# Pending action patterns
PENDING_ACTION_PATTERNS = {
    "AskUserQuestion": re.compile(
        r"AskUser(?:Question)?[:\s]+(.{10,200})",
        re.IGNORECASE | re.DOTALL,
    ),
    "awaiting_input": re.compile(
        r"(?:waiting for|awaiting|need)\s+(?:user\s+)?(?:input|response|decision|approval)",
        re.IGNORECASE,
    ),
    "review_prompt": re.compile(
        r"(?:would you like to|do you want to|shall I)\s+(.{10,150})",
        re.IGNORECASE,
    ),
}

# Confidence scoring weights
CONFIDENCE_WEIGHTS = {
    "clear_trigger": 0.4,      # Found explicit /PACT:* command
    "step_marker": 0.2,        # Found step marker in content
    "agent_invocation": 0.2,   # Found Task call to PACT agent
    "pending_action": 0.1,     # Found pending action indicator
    "context_richness": 0.1,   # Found context elements (PR#, task summary)
}


def compile_workflow_patterns() -> dict[str, WorkflowPattern]:
    """
    Compile all workflow patterns into WorkflowPattern objects.

    Returns:
        Dict mapping workflow name to compiled WorkflowPattern
    """
    patterns = {}
    for name in TRIGGER_PATTERNS:
        patterns[name] = WorkflowPattern(
            name=name,
            trigger_pattern=TRIGGER_PATTERNS[name],
            step_markers=STEP_MARKERS.get(name, []),
            termination_signals=TERMINATION_SIGNALS.get(name, []),
            context_extractors=CONTEXT_EXTRACTORS,
        )
    return patterns


# Pre-compiled workflow patterns for use by other modules
WORKFLOW_PATTERNS = compile_workflow_patterns()


def is_termination_signal(content: str, workflow_name: str) -> bool:
    """
    Check if content contains a termination signal for the given workflow.

    Args:
        content: Text content to check
        workflow_name: Name of the workflow to check termination for

    Returns:
        True if a termination signal is found
    """
    signals = TERMINATION_SIGNALS.get(workflow_name, [])
    for signal_pattern in signals:
        if re.search(signal_pattern, content, re.IGNORECASE | re.MULTILINE):
            return True
    return False


def extract_context_value(content: str, context_key: str) -> str | None:
    """
    Extract a context value from content using the appropriate pattern.

    Args:
        content: Text content to search
        context_key: Key identifying which extractor to use

    Returns:
        Extracted value or None if not found
    """
    pattern = CONTEXT_EXTRACTORS.get(context_key)
    if pattern:
        match = pattern.search(content)
        if match:
            return match.group(1).strip()
    return None
