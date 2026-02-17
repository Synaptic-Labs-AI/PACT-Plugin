#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/memory_prompt.py
Summary: Stop hook that prompts agent to save memories after significant work.
Used by: Claude Code settings.json Stop hook

Analyzes session transcript for save-worthy content:
- PACT phase completions (agent invocations)
- Decisions made
- Lessons learned mentions
- Blockers encountered

Input: JSON from stdin with session transcript/context
Output: JSON with `systemMessage` prompting to save memory if relevant content found
"""

import json
import re
import sys
from typing import Dict, List


# PACT agent patterns indicating phase work
PACT_AGENTS = [
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
    "pact-memory-agent",
]

# Patterns indicating decisions were made
DECISION_PATTERNS = [
    r"(?:decided|chose|selected|opted)\s+(?:to|for|on)",
    r"(?:decision|rationale|because|reason):",
    r"trade-?off",
    r"(?:went with|picked|selected)",
    r"alternative(?:s)?(?:\s+considered)?:",
]

# Patterns indicating lessons learned
LESSON_PATTERNS = [
    r"lesson(?:s)?\s+learned",
    r"(?:learned|discovered|found out)\s+that",
    r"(?:what|things)\s+(?:worked|didn't work)",
    r"(?:tip|insight):",
    r"(?:important|key)\s+(?:to|that|finding)",
    r"(?:should have|next time)",
]

# Patterns indicating blockers
BLOCKER_PATTERNS = [
    r"blocker",
    r"blocked\s+(?:by|on)",
    r"(?:ran into|hit)\s+(?:a\s+)?(?:problem|issue|error)",
    r"(?:stuck|stalled)\s+on",
]


def detect_pact_agents(transcript: str) -> List[str]:
    """Detect which PACT agents were invoked in the session."""
    transcript_lower = transcript.lower()
    return [agent for agent in PACT_AGENTS if agent in transcript_lower]


def detect_patterns(transcript: str, patterns: List[str]) -> bool:
    """Check if any patterns match in the transcript."""
    transcript_lower = transcript.lower()
    return any(re.search(pattern, transcript_lower) for pattern in patterns)


def analyze_transcript(transcript: str) -> Dict:
    """Analyze transcript for memory-worthy content."""
    return {
        "agents": detect_pact_agents(transcript),
        "has_decisions": detect_patterns(transcript, DECISION_PATTERNS),
        "has_lessons": detect_patterns(transcript, LESSON_PATTERNS),
        "has_blockers": detect_patterns(transcript, BLOCKER_PATTERNS),
    }


def should_prompt_memory(analysis: Dict) -> bool:
    """Determine if we should prompt for memory save."""
    return bool(
        analysis["agents"] or
        analysis["has_decisions"] or
        analysis["has_lessons"] or
        analysis["has_blockers"]
    )


def format_prompt(analysis: Dict) -> str:
    """Format the memory save prompt message."""
    lines = ["⚠️ MANDATORY: You MUST delegate to pact-memory-agent NOW to save session context:"]

    if analysis["agents"]:
        agent_list = ", ".join(analysis["agents"])
        lines.append(f"- PACT work completed with: {agent_list}")

    if analysis["has_decisions"]:
        lines.append("- Decisions made (MUST capture rationale + alternatives)")

    if analysis["has_lessons"]:
        lines.append("- Lessons learned (MUST preserve for future sessions)")

    if analysis["has_blockers"]:
        lines.append("- Blockers resolved (MUST document for next time)")

    lines.append("")
    lines.append("This is NOT optional. Failure to save = lost context = repeated work.")

    return "\n".join(lines)


def main():
    """Main entry point for the Stop hook."""
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            input_data = {}

        transcript = input_data.get("transcript", "")

        # Skip if no transcript or very short session
        if not transcript or len(transcript) < 500:
            sys.exit(0)

        analysis = analyze_transcript(transcript)

        if should_prompt_memory(analysis):
            prompt_message = format_prompt(analysis)
            output = {"systemMessage": prompt_message}
            print(json.dumps(output))

        sys.exit(0)

    except Exception as e:
        print(f"Hook warning (memory_prompt): {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
