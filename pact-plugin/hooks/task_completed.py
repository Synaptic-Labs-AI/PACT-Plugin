#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/task_completed.py
Summary: TaskCompleted hook for PACT v3.0 Agent Teams.
Used by: hooks.json TaskCompleted event registration

Fires when a task is being marked complete. Currently a placeholder
that always allows completion (exit 0). The skip_prefixes structure
is in place for when validation is added.

Input (env vars): TASK_ID, TASK_SUBJECT, TASK_DESCRIPTION, TEAMMATE_NAME, TEAM_NAME

Note: This hook needs empirical validation. The TaskCompleted event
behavior (env vars available, metadata access, exit code semantics)
is based on platform documentation but has not been tested in production.
TODO: Once TaskGet access from hooks is confirmed empirically,
this hook should validate that the task has proper handoff metadata
(produced, decisions, uncertainties, integration_points, open_questions)
before allowing completion (exit 2 to prevent completion with feedback).
"""
import os
import sys


def main():
    # PLACEHOLDER: This hook is registered but performs no validation yet.
    # Handoff validation requires TaskGet access from hooks, which has
    # not been empirically confirmed. Once confirmed, add validation
    # that reads task metadata and checks for handoff fields.

    task_subject = os.environ.get("TASK_SUBJECT", "")
    teammate_name = os.environ.get("TEAMMATE_NAME", "")

    # Only validate tasks completed by PACT specialists
    if not teammate_name or not teammate_name.startswith("pact-"):
        sys.exit(0)

    # Skip validation for phase tasks and signal tasks (completed by orchestrator)
    skip_prefixes = (
        "PREPARE:", "ARCHITECT:", "CODE:", "TEST:",
        "BLOCKER:", "QUARANTINE:",  # Reserved for future quarantine/isolation workflow
        "HALT:", "ALERT:",
    )
    if any(task_subject.startswith(p) for p in skip_prefixes):
        sys.exit(0)

    # Placeholder: allow completion without validation
    sys.exit(0)


if __name__ == "__main__":
    main()
