#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/peer_inject.py
Summary: SubagentStart hook that injects peer teammate list into newly
         spawned PACT agents via additionalContext.
Used by: hooks.json SubagentStart hook (matcher: pact-* agent types)

Replaces the manual pattern of listing peer names in task descriptions.
Agents automatically know who else is on the team.

Input: JSON from stdin with agent_id, agent_type
Output: JSON with hookSpecificOutput.additionalContext
"""

import json
import re
import sys
from pathlib import Path

import shared.pact_context as pact_context
from shared.pact_context import get_team_name

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


_TEACHBACK_REMINDER = (
    "\n\nSend your teachback before any Edit/Write/Agent/NotebookEdit tool "
    "call. Tasks at variety >= 7 must include a structured "
    "metadata.teachback_submit written via TaskUpdate — not just a "
    "teachback_sent flag — so the teachback_gate hook can validate the "
    "submit schema and route you to the `active` state once the lead "
    "approves. See the teachback state machine + schema in the skills "
    "loaded by /PACT:teammate-bootstrap (pact-teachback + teammate-bootstrap "
    "commands). In Phase 1 the gate is advisory (deny reasons arrive as "
    "systemMessage but tools still run); Phase 2 flips to blocking so "
    "non-compliant tool calls are denied. Write your teachback correctly "
    "now so Phase 2 does not break your workflow later."
)


_BOOTSTRAP_PRELUDE_TEMPLATE = (
    "YOUR PACT ROLE: teammate ({agent_name}).\n\n"
    "YOUR FIRST ACTION (YOU MUST DO THIS IMMEDIATELY): invoke Skill(\"PACT:teammate-bootstrap\"). "
    "This loads the team communication protocol, teachback standards, memory retrieval, "
    "and algedonic reference. If your context is later compacted and the bootstrap content "
    "is no longer present, re-invoke the skill before continuing implementation.\n\n"
)


def _sanitize_agent_name(agent_name: str) -> str:
    """Strip characters from agent_name that could break out of the
    PACT ROLE marker format.

    SECURITY (cycle 2 minor item 12): the prelude template interpolates
    agent_name into `YOUR PACT ROLE: teammate ({agent_name}).` Without
    sanitization, an agent_name containing a newline could inject a
    second `YOUR PACT ROLE: orchestrator` line into additionalContext,
    causing a teammate to self-identify as the orchestrator under the
    routing block's substring check.

    Stripped characters:
      - newline (\\n) and carriage return (\\r): prevent line-break
        injection that could spawn a fake marker line
      - close-paren ()): prevent closing the parenthetical early so
        downstream content can claim to be a different role

    The fallback for empty/None agent_name is "unknown" — same as
    before this hardening.

    Note: this is producer-side sanitization. The line-anchor consumer
    check in PACT_ROUTING_BLOCK is the second layer of defense
    (cycle 2 minor item 15) — together they provide defense in depth
    against marker spoofing via either malicious agent names or
    embedded prose containing the marker phrase.
    """
    if not agent_name:
        return "unknown"
    # Strip all C0 control chars (0x00-0x1F), DEL (0x7F), and Unicode
    # line terminators NEL (U+0085), LINE SEPARATOR (U+2028), PARAGRAPH
    # SEPARATOR (U+2029). The Unicode terminators are recognized by
    # `str.splitlines()` and by LLM tokenizers — a name containing
    # U+2028 can inject a fake line into the PACT ROLE prelude
    # template, bypassing the line-anchor consumer check that is the
    # second layer of defense (see security-engineer memory
    # patterns_symmetric_sanitization.md). Matches the sibling filter
    # in session_state._sanitize_rendered_string.
    sanitized = re.sub(r"[\x00-\x1f\x7f\u0085\u2028\u2029]", "_", agent_name)
    return sanitized.replace(")", "_")


def get_peer_context(
    agent_type: str,
    team_name: str,
    agent_name: str = "",
    teams_dir: str | None = None,
) -> str | None:
    """
    Build peer context string for a newly spawned agent.

    Prepends a bootstrap prelude (PACT ROLE marker + YOUR FIRST ACTION skill
    invocation instruction) and appends a teachback timing reminder
    after the peer list. The PACT ROLE marker is the stable substring
    used by lead routing logic; empty agent_name falls back to "unknown".

    Args:
        agent_type: The spawning agent's type (e.g., "pact-backend-coder")
        team_name: Current team name
        agent_name: The spawning agent's unique name (e.g., "backend-coder-1")
        teams_dir: Override for teams directory (for testing)

    Returns:
        Context string with bootstrap prelude, peer list, and teachback
        reminder, or None if no team context
    """
    if not team_name:
        return None

    if teams_dir is None:
        teams_dir = str(Path.home() / ".claude" / "teams")

    config_path = Path(teams_dir) / team_name / "config.json"
    if not config_path.exists():
        return None

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return None

    members = config.get("members", [])

    # Sanitize agent_name once up-front so the peer-list filter AND the
    # prelude interpolation use the same cleaned value. Using the raw
    # agent_name in the filter would cause self-exclusion to fail if the
    # raw name contained hostile characters (e.g., embedded newlines) —
    # a cosmetic but real degradation of the peer list.
    safe_name = _sanitize_agent_name(agent_name)

    if safe_name and safe_name != "unknown":
        # Filter by exact (sanitized) name — excludes only the spawning
        # agent itself. Team members are registered under their canonical
        # names in the team config, so matching against the sanitized
        # form is correct under normal conditions. Under attack, both
        # sides flow through the same sanitization and remain consistent.
        # Sanitize the emitted name so a hostile config entry cannot inject
        # a line-anchored `YOUR PACT ROLE:` marker via the peer list.
        peers = [_sanitize_agent_name(m["name"]) for m in members if m.get("name") != safe_name]
    else:
        # Fallback: filter by agentType. This excludes ALL agents of the same
        # type, not just the spawning agent. This is a known limitation when
        # the hook input does not include agent_name/agent_id.
        peers = [_sanitize_agent_name(m["name"]) for m in members if m.get("agentType") != agent_type]

    if not peers:
        peer_context = "You are the only active teammate on this team."
    else:
        peer_list = ", ".join(peers)
        peer_context = (
            f"Active teammates on your team: {peer_list}\n"
            f"You can message them via SendMessage for shared artifacts or blocking questions."
        )

    prelude = _BOOTSTRAP_PRELUDE_TEMPLATE.format(agent_name=safe_name)
    return prelude + peer_context + _TEACHBACK_REMINDER


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    pact_context.init(input_data)
    agent_type = input_data.get("agent_type", "")
    # Only accept agent_name here. agent_id is a UUID and team members are
    # registered in the team config under their canonical names, not UUIDs —
    # falling back to agent_id would make the self-exclusion filter in
    # get_peer_context() fail to match anything, and the intended agentType
    # fallback (which excludes ALL peers of the same type) would become
    # unreachable. Leave agent_name empty when absent so get_peer_context's
    # agentType fallback fires as originally designed.
    agent_name = input_data.get("agent_name", "")
    team_name = get_team_name()

    context = get_peer_context(
        agent_type=agent_type,
        team_name=team_name,
        agent_name=agent_name,
    )

    if context:
        output = {
            "hookSpecificOutput": {
                "additionalContext": context
            }
        }
        print(json.dumps(output))
    else:
        print(_SUPPRESS_OUTPUT)

    sys.exit(0)


if __name__ == "__main__":
    main()
