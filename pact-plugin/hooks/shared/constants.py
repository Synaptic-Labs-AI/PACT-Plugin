"""
Location: pact-plugin/hooks/shared/constants.py
Summary: Shared constants used across multiple PACT hooks.
Used by: memory_retrieve.py, handoff_gate.py, and their tests.

Single source of truth for the PACT work agents list, eliminating
duplication between hooks that need to identify PACT specialist agents.
"""

# PACT agents that do substantive work requiring memory retrieval and saves.
# Excludes pact-memory-agent (which manages memory, not consumes it).
PACT_WORK_AGENTS = [
    "pact-preparer",
    "pact-architect",
    "pact-backend-coder",
    "pact-frontend-coder",
    "pact-database-engineer",
    "pact-devops-engineer",
    "pact-n8n",
    "pact-test-engineer",
    "pact-security-engineer",
    "pact-qa-engineer",
]
