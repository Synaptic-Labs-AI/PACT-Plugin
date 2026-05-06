#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/peer_inject.py
Summary: SubagentStart hook that injects peer teammate list into newly
         spawned PACT agents via additionalContext.
Used by: hooks.json SubagentStart hook (matcher: pact-* agent types)

Replaces the manual pattern of listing peer names in task descriptions.
Agents automatically know who else is on the team.

SACROSANCT: every raisable path in main() is wrapped in try/except that
defaults to passthrough (exit 0 with suppressOutput). A hook bug must
never block a SubagentStart event. Mirrors the fail-open contract
documented in bootstrap_gate.py and bootstrap_prompt_gate.py.

Input: JSON from stdin with agent_id, agent_type
Output: JSON with hookSpecificOutput.additionalContext
"""

import json
import re
import sys
from pathlib import Path

import shared.pact_context as pact_context
from shared.pact_context import get_team_name
from shared.plugin_manifest import format_plugin_banner

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


_TEACHBACK_REMINDER = (
    "\n\nTEACHBACK TIMING: Submit your teachback via metadata.teachback_submit "
    "on Task A BEFORE any Edit/Write/Bash calls. Teachback is a gate — "
    "Task B stays blocked until the team-lead accepts. See the "
    "pact-teachback skill for the exact format. If you haven't submitted "
    "a teachback yet, do it now before any implementation work."
)


_COMPLETION_AUTHORITY_NOTE = (
    "\n\nCOMPLETION AUTHORITY: You do NOT mark your own tasks `completed`. "
    "When your work is done, write your HANDOFF (or teachback metadata) to "
    "the task and remain `in_progress`. The team-lead reads your output, judges "
    "acceptance, and transitions status to `completed` only on accept. "
    "Your dispatch may be a Task A (teachback) + Task B (work) pair: claim A, "
    "submit teachback, idle on `intentional_wait{reason=awaiting_lead_completion}`. "
    "Do NOT begin Task B until A.status == 'completed' (team-lead's wake-signal "
    "SendMessage confirms; you cannot self-wake to poll TaskList while idle)."
)


_BOOTSTRAP_PRELUDE_TEMPLATE = (
    "YOUR PACT ROLE: teammate ({agent_name}).\n\n"
    "TEAM COMMUNICATION: read protocols/pact-communication-charter.md "
    "for the inter-agent messaging contract before sending teammate messages.\n\n"
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

    Prepends a bootstrap prelude (PACT ROLE marker + team communication
    charter cross-ref) and appends a teachback timing reminder and
    completion-authority note after the peer list. The PACT ROLE marker
    is the stable substring used by team-lead routing logic; empty
    agent_name falls back to "unknown".

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
        peers = [m["name"] for m in members if m.get("name") != safe_name]
    else:
        # Fallback: filter by agentType. This excludes ALL agents of the same
        # type, not just the spawning agent. This is a known limitation when
        # the hook input does not include agent_name/agent_id.
        peers = [m["name"] for m in members if m.get("agentType") != agent_type]

    if not peers:
        peer_context = "You are the only active teammate on this team."
    else:
        peer_list = ", ".join(peers)
        peer_context = (
            f"Active teammates on your team: {peer_list}\n"
            f"You can message them via SendMessage for shared artifacts or blocking questions."
        )

    prelude = _BOOTSTRAP_PRELUDE_TEMPLATE.format(agent_name=safe_name)
    # Output ordering: prelude → peer_context → "\n\n" → plugin banner →
    # _TEACHBACK_REMINDER → _COMPLETION_AUTHORITY_NOTE. The plugin banner
    # is a single line with no leading/trailing newlines, so an explicit
    # "\n\n" separator goes between peer_context and the banner.
    # _TEACHBACK_REMINDER and _COMPLETION_AUTHORITY_NOTE each begin with
    # "\n\n", preserving visual spacing through the trailing reminders.
    return (
        prelude
        + peer_context
        + "\n\n"
        + format_plugin_banner()
        + _TEACHBACK_REMINDER
        + _COMPLETION_AUTHORITY_NOTE
    )


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        # input_data may not be a dict (e.g., parseable JSON `123` or `[]`);
        # downstream pact_context.init + .get() raise AttributeError on
        # non-dict input. The outer except below catches any such raise
        # and falls open with suppressOutput, mirroring the SACROSANCT
        # fail-open pattern in bootstrap_gate.py and bootstrap_prompt_gate.py.
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
    except Exception:
        # Any exception in the build path → fail-open with suppressOutput.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

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
