#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/memory_retrieve.py
Summary: SubagentStart hook that injects lightweight memory retrieval instructions
         into newly spawned PACT agents via additionalContext.
Used by: hooks.json SubagentStart hook (matcher: pact-* agent types)

Agents automatically receive ~20-30 lines of retrieval instructions telling them
how to search pact-memory for prior context and report findings in their teachback.
This replaces the old MLP inline blocks that agents had to read manually.

Input: JSON from stdin with agent_type, agent_name fields
Output: JSON with hookSpecificOutput.additionalContext
"""

import json
import sys


# PACT agents that do substantive work requiring memory retrieval.
# Mirrors handoff_gate.py PACT_WORK_AGENTS (minus pact-memory-agent, which is removed).
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

# Map agent types to domain hints for search queries
DOMAIN_HINTS = {
    "pact-preparer": "requirements preparation research",
    "pact-architect": "architecture design patterns",
    "pact-backend-coder": "backend implementation",
    "pact-frontend-coder": "frontend implementation UI",
    "pact-database-engineer": "database schema queries migrations",
    "pact-devops-engineer": "devops CI/CD infrastructure",
    "pact-n8n": "n8n workflow automation",
    "pact-test-engineer": "testing quality assurance",
    "pact-security-engineer": "security review vulnerabilities",
    "pact-qa-engineer": "QA runtime verification",
}


def is_pact_work_agent(agent_type: str) -> bool:
    """Check if this agent type is a PACT work agent that should receive retrieval context."""
    if not agent_type:
        return False
    agent_lower = agent_type.lower()
    return any(agent_lower == agent or agent_lower.startswith(agent + "-") for agent in PACT_WORK_AGENTS)


def get_domain_hint(agent_type: str) -> str:
    """
    Derive a domain hint from the agent type for use in search queries.

    Args:
        agent_type: The spawning agent's type (e.g., "pact-backend-coder")

    Returns:
        Domain hint string for search queries
    """
    if not agent_type:
        return "general"
    agent_lower = agent_type.lower()
    # Try exact match first, then prefix match for scope-suffixed names
    if agent_lower in DOMAIN_HINTS:
        return DOMAIN_HINTS[agent_lower]
    for agent, hint in DOMAIN_HINTS.items():
        if agent_lower.startswith(agent):
            return hint
    return "general"


def build_retrieval_context(agent_type: str) -> str:
    """
    Build the retrieval instructions to inject into agent context.

    Args:
        agent_type: The spawning agent's type

    Returns:
        Retrieval instruction text (~20-30 lines)
    """
    domain = get_domain_hint(agent_type)

    return f"""## Memory Retrieval (Automatic)

Search for prior context relevant to your task before starting work.
This helps you avoid repeating past mistakes and build on existing knowledge.

**How to search:**
```bash
cd ~/.claude/pact-memory && python -m scripts.cli search "{{your task topic}} {domain}"
```

Replace `{{your task topic}}` with keywords from your assigned task (e.g., "auth endpoints", "scroll progress", "schema migration").

**Include results in your teachback as:**
```
MEMORY REPORT:
- Searched for: "{{query}}"
- Found: {{N}} relevant memories
- Key context: {{summary or "None -- starting fresh"}}
```

If the pact-memory database does not exist or the search returns an error, report:
```
MEMORY REPORT:
- Searched for: "{{query}}"
- Found: 0 (database not initialized)
- Key context: None -- starting fresh
```

Domain hint for your role: {domain}"""


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    agent_type = input_data.get("agent_type", "")

    if not is_pact_work_agent(agent_type):
        sys.exit(0)

    context = build_retrieval_context(agent_type)

    output = {
        "hookSpecificOutput": {
            "additionalContext": context
        }
    }
    print(json.dumps(output))

    sys.exit(0)


if __name__ == "__main__":
    main()
