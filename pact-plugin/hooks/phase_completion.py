#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/phase_completion.py
Summary: Stop hook that verifies phase completion and reminds about decision logs.
Used by: Claude Code settings.json Stop hook

Checks for CODE phase completion without decision logs and reminds about
documentation and testing requirements.

With Task integration, phase completion is detected via Task statuses first,
falling back to transcript parsing if TaskList unavailable.

Input: JSON from stdin with session transcript/context
Output: JSON with `systemMessage` for reminders if needed
"""

import json
import sys
import os
from pathlib import Path
from typing import Any

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

# Import shared Task utilities (DRY - used by multiple hooks)
from shared.task_utils import get_task_list


# Indicators that CODE phase work was performed (for transcript fallback)
CODE_PHASE_INDICATORS = [
    "pact-backend-coder",
    "pact-frontend-coder",
    "pact-database-engineer",
    "pact-devops-engineer",
    "pact_backend_coder",
    "pact_frontend_coder",
    "pact_database_engineer",
    "pact_devops_engineer",
]

# Terms indicating decision log was mentioned or created
DECISION_LOG_MENTIONS = [
    "decision-log",
    "decision log",
    "decision_log",
    "decisionlog",
    "docs/decision-logs",
    "decision-logs/",
]


def check_phase_completion_via_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Check phase completion status using Task system.

    Analyzes Task statuses to determine:
    - If CODE phase is completed
    - If TEST phase has started
    - Any phase completion reminders needed

    Args:
        tasks: List of all tasks

    Returns:
        Dict with:
        - code_completed: bool
        - test_started: bool
        - test_completed: bool
        - reminders: list of reminder messages
    """
    result = {
        "code_completed": False,
        "test_started": False,
        "test_completed": False,
        "reminders": [],
    }

    code_phase = None
    test_phase = None

    for task in tasks:
        subject = task.get("subject", "")
        status = task.get("status", "")

        if subject.startswith("CODE:"):
            code_phase = task
            if status == "completed":
                result["code_completed"] = True

        if subject.startswith("TEST:"):
            test_phase = task
            if status == "in_progress":
                result["test_started"] = True
            elif status == "completed":
                result["test_started"] = True
                result["test_completed"] = True

    # Generate reminders based on Task state
    if result["code_completed"] and not result["test_started"]:
        result["reminders"].append(
            "TEST Phase Reminder: CODE phase completed. Consider invoking "
            "pact-test-engineer to verify the implementation."
        )

    if test_phase and test_phase.get("status") == "pending":
        result["reminders"].append(
            "TEST Phase Reminder: TEST phase is pending (blocked). "
            "Check blockedBy to see what needs to complete first."
        )

    return result


# -----------------------------------------------------------------------------
# Transcript Fallback (Legacy Detection)
# -----------------------------------------------------------------------------

def check_for_code_phase_activity(transcript: str) -> bool:
    """
    Determine if CODE phase agents were invoked in this session (fallback).

    Args:
        transcript: The session transcript

    Returns:
        True if CODE phase activity detected
    """
    transcript_lower = transcript.lower()
    return any(indicator in transcript_lower for indicator in CODE_PHASE_INDICATORS)


def check_decision_log_mentioned(transcript: str) -> bool:
    """
    Check if decision logs were mentioned in the transcript.

    Args:
        transcript: The session transcript

    Returns:
        True if decision logs were discussed or created
    """
    transcript_lower = transcript.lower()
    return any(mention in transcript_lower for mention in DECISION_LOG_MENTIONS)


def check_decision_logs_exist(project_dir: str) -> bool:
    """
    Check if any decision logs exist in the project.

    Args:
        project_dir: The project root directory

    Returns:
        True if decision-logs directory exists and contains files
    """
    decision_logs_dir = Path(project_dir) / "docs" / "decision-logs"
    if not decision_logs_dir.is_dir():
        return False

    # Check for any markdown files in the directory
    return any(decision_logs_dir.glob("*.md"))


def check_for_test_reminders(transcript: str) -> bool:
    """
    Check if testing was discussed for completed code work.

    Args:
        transcript: The session transcript

    Returns:
        True if testing appears to be addressed
    """
    transcript_lower = transcript.lower()
    test_indicators = [
        "pact-test-engineer",
        "test engineer",
        "testing",
        "unit test",
        "integration test",
        "test coverage",
    ]
    return any(indicator in transcript_lower for indicator in test_indicators)


def main():
    """
    Main entry point for the Stop hook.

    Strategy:
    1. Primary: Check Task statuses for phase completion (Task integration)
    2. Fallback: Check transcript for CODE phase indicators

    Reminds about decision logs and testing if appropriate.
    """
    try:
        # Read input from stdin
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            input_data = {}

        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
        transcript = input_data.get("transcript", "")

        messages = []
        was_code_phase = False

        # ---------------------------------------------------------------------
        # Primary: Check Task statuses (Task integration)
        # ---------------------------------------------------------------------
        tasks = get_task_list()

        if tasks:
            phase_status = check_phase_completion_via_tasks(tasks)

            # Add any Task-derived reminders
            messages.extend(phase_status.get("reminders", []))

            # Track CODE phase completion for decision log check
            was_code_phase = phase_status.get("code_completed", False)

        # ---------------------------------------------------------------------
        # Fallback: Check transcript if no Task state
        # ---------------------------------------------------------------------
        elif transcript:
            was_code_phase = check_for_code_phase_activity(transcript)

            if was_code_phase:
                # Check if testing was addressed (transcript-based)
                testing_discussed = check_for_test_reminders(transcript)
                if not testing_discussed:
                    messages.append(
                        "TEST Phase Reminder: Consider invoking pact-test-engineer "
                        "to verify the implementation."
                    )

        # ---------------------------------------------------------------------
        # Common checks (regardless of source)
        # ---------------------------------------------------------------------
        if was_code_phase:
            # Check if decision logs were addressed
            decision_log_mentioned = transcript and check_decision_log_mentioned(transcript)
            decision_logs_exist = check_decision_logs_exist(project_dir)

            if not decision_log_mentioned and not decision_logs_exist:
                messages.append(
                    "CODE Phase Reminder: Decision logs should be created at "
                    "docs/decision-logs/{feature}-{domain}.md to document key "
                    "implementation decisions and trade-offs."
                )

        # Output messages if any reminders are needed
        if messages:
            output = {
                "systemMessage": " | ".join(messages)
            }
            print(json.dumps(output))

        sys.exit(0)

    except Exception as e:
        # Don't block on errors - just warn
        print(f"Hook warning (phase_completion): {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
