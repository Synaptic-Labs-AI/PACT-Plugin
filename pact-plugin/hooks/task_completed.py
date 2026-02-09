#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/task_completed.py
Summary: TaskCompleted hook for PACT v3.0 Agent Teams.
Used by: hooks.json TaskCompleted event registration

Fires when a task is being marked complete. Validates that
the task has proper handoff metadata before allowing completion.
Exit code 2 prevents completion and sends feedback.

Input (env vars): TASK_ID, TASK_SUBJECT, TASK_DESCRIPTION, TEAMMATE_NAME, TEAM_NAME

Note: This hook needs empirical validation. The TaskCompleted event
behavior (env vars available, metadata access, exit code semantics)
is based on platform documentation but has not been tested in production.
"""
import os
import sys


HANDOFF_FIELDS = ["produced", "decisions", "uncertainties",
                  "integration_points", "open_questions"]


def main():
    task_subject = os.environ.get("TASK_SUBJECT", "")
    teammate_name = os.environ.get("TEAMMATE_NAME", "")

    # Only validate tasks completed by PACT specialists
    if not teammate_name or not teammate_name.startswith("pact-"):
        sys.exit(0)

    # Skip validation for phase tasks and signal tasks (completed by orchestrator)
    skip_prefixes = (
        "PREPARE:", "ARCHITECT:", "CODE:", "TEST:",
        "BLOCKER:", "QUARANTINE:", "HALT:", "ALERT:",
    )
    if any(task_subject.startswith(p) for p in skip_prefixes):
        sys.exit(0)

    # TODO: Read task metadata and validate handoff fields.
    # This requires TaskGet access from within the hook, which
    # may not be available. Needs empirical validation.
    # For now, this is a placeholder that allows completion.
    sys.exit(0)


if __name__ == "__main__":
    main()
