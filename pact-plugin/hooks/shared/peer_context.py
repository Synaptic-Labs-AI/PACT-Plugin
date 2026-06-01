#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/peer_context.py
Summary: Shared builder for the peer-context additionalContext block injected
         into newly active PACT teammates (peer list + charter cross-ref +
         teachback reminder + completion-authority note), plus the role-marker
         prelude templates and agent-name sanitizer.
Used by: peer_inject.py (SubagentStart, in-process teammates — re-exports these
         names) AND session_init.py (SessionStart teammate-branch, separate-
         process/tmux teammates). One SSOT builder so the two call-sites cannot
         drift apart.

The single `include_role_marker` switch is what lets ONE builder serve both
surfaces: SubagentStart wants the full prelude (role marker + charter);
SessionStart's teammate-branch wants the charter + body WITHOUT the role
marker (the spawn prompt already owns the role, and session_init lacks
agent_name under tmux, so re-claiming a role would reintroduce the mis-roling
this relocation fixes).
"""

import json
import os
import re
from pathlib import Path

from shared.plugin_manifest import format_plugin_banner


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


# Prelude split into two independently-emittable lines so the role marker is
# CONDITIONAL (see include_role_marker on get_peer_context):
#   _ROLE_MARKER_TEMPLATE  — the "YOUR PACT ROLE: teammate (...)" line; emitted
#                            ONLY when include_role_marker=True.
#   _CHARTER_CROSSREF_LINE — the communication-charter pointer; emitted on BOTH
#                            surfaces (every spawn needs the messaging contract).
_ROLE_MARKER_TEMPLATE = "YOUR PACT ROLE: teammate ({agent_name}).\n\n"

_CHARTER_CROSSREF_LINE = (
    "TEAM COMMUNICATION: read protocols/pact-communication-charter.md "
    "for the inter-agent messaging contract before sending teammate messages.\n\n"
)

# Back-compat composite — byte-identical to the pre-split single template.
# Retained so the SubagentStart default path AND existing import sites/tests
# (which reference _BOOTSTRAP_PRELUDE_TEMPLATE directly) stay unchanged.
_BOOTSTRAP_PRELUDE_TEMPLATE = _ROLE_MARKER_TEMPLATE + _CHARTER_CROSSREF_LINE


def _sanitize_agent_name(agent_name: str) -> str:
    """Strip characters from agent_name that could break out of the
    PACT ROLE marker format.

    SECURITY (cycle 2 minor item 12): the prelude template interpolates
    agent_name into `YOUR PACT ROLE: teammate ({agent_name}).` Without
    sanitization, an agent_name containing a newline could inject a
    second `YOUR PACT ROLE: orchestrator` line into additionalContext,
    causing a teammate to self-identify as the orchestrator.

    Stripped characters:
      - newline (\\n) and carriage return (\\r): prevent line-break
        injection that could spawn a fake marker line
      - close-paren ()): prevent closing the parenthetical early so
        downstream content can claim to be a different role

    The fallback for empty/None agent_name is "unknown" — same as
    before this hardening.

    Note: this is producer-side sanitization — it defends against marker
    spoofing via either malicious agent names or embedded prose containing
    the marker phrase.
    """
    if not agent_name:
        return "unknown"
    # Strip all C0 control chars (0x00-0x1F), DEL (0x7F), and Unicode
    # line terminators NEL (U+0085), LINE SEPARATOR (U+2028), PARAGRAPH
    # SEPARATOR (U+2029). The Unicode terminators are recognized by
    # `str.splitlines()` and by LLM tokenizers — a name containing
    # U+2028 can inject a fake line into the PACT ROLE prelude
    # template (see security-engineer memory
    # patterns_symmetric_sanitization.md). Matches the sibling filter
    # in session_state._sanitize_rendered_string.
    sanitized = re.sub(r"[\x00-\x1f\x7f\u0085\u2028\u2029]", "_", agent_name)
    return sanitized.replace(")", "_")


def get_peer_context(
    agent_type: str,
    team_name: str,
    agent_name: str = "",
    teams_dir: str | None = None,
    include_role_marker: bool = True,
) -> str | None:
    """
    Build peer context string for a newly active agent.

    Prepends a bootstrap prelude and appends a teachback timing reminder and
    completion-authority note after the peer list.

    The prelude is gated by ``include_role_marker``:
      - ``True`` (default): role marker (``YOUR PACT ROLE: teammate (...)``) +
        charter cross-ref + peer body. This is the SubagentStart surface
        (peer_inject); output is byte-identical to the pre-decomposition build.
      - ``False``: charter cross-ref + peer body, NO role marker. This is the
        SessionStart teammate-branch (session_init) — the spawn prompt already
        owns the role, and session_init lacks agent_name under tmux, so the
        marker is deliberately not re-emitted (avoids the mis-roling the
        relocation fixes). The marker is LLM-prose, not an authz token; role
        gating keys on agent_type, so dropping it is safe.

    The PACT ROLE marker (when present) is the stable substring used by
    team-lead routing logic; empty agent_name falls back to "unknown".

    Args:
        agent_type: The spawning agent's type (e.g., "pact-backend-coder")
        team_name: Current team name
        agent_name: The spawning agent's unique name (e.g., "backend-coder-1")
        teams_dir: Override for teams directory (for testing)
        include_role_marker: Emit the role-marker prelude line (default True)

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
        # O2 (#806): the EMITTED peer name is ALSO sanitized (symmetric with
        # the self name) so a hostile member name cannot inject a fake role
        # marker or line break into the peer list; and the exclusion now
        # compares sanitized-vs-sanitized (closing the self-exclusion gap
        # under a hostile self name). Normal names are sanitize-invariant, so
        # this is byte-identical for ordinary configs.
        peers = [
            _sanitize_agent_name(m["name"])
            for m in members
            if _sanitize_agent_name(m["name"]) != safe_name
        ]
    else:
        # Fallback: filter by agentType. This excludes ALL agents of the same
        # type, not just the spawning agent. This is a known limitation when
        # the hook input does not include agent_name/agent_id. O2 (#806):
        # the emitted peer name is sanitized here too.
        peers = [
            _sanitize_agent_name(m["name"])
            for m in members
            if m.get("agentType") != agent_type
        ]

    if not peers:
        peer_context = "You are the only active teammate on this team."
    else:
        peer_list = ", ".join(peers)
        peer_context = (
            f"Active teammates on your team: {peer_list}\n"
            f"You can message them via SendMessage for shared artifacts or blocking questions."
        )

    if include_role_marker:
        # SubagentStart surface: full prelude, byte-identical to the
        # pre-decomposition build (_BOOTSTRAP_PRELUDE_TEMPLATE is the
        # role-marker line + charter line; only the marker line carries
        # the {agent_name} placeholder).
        prelude = _BOOTSTRAP_PRELUDE_TEMPLATE.format(agent_name=safe_name)
    else:
        # SessionStart teammate-branch: charter line only, no role marker.
        prelude = _CHARTER_CROSSREF_LINE
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


# ───────── LEAD-team resolver for separate-process teammates (#806 O1) ─────────
# A separate-process (tmux/iTerm2) teammate's session_init cannot derive the
# LEAD's team from generate_team_name(input_data) — that yields the teammate's
# OWN session hash — and the lead's team is NOT carried in the hook's env or
# stdin (it lives only in the parent claude process's argv, unreachable to a
# hook subprocess). BUT the harness records each teammate's terminal pane id in
# the team config's members[].tmuxPaneId, and the teammate's own pane id is in
# its environment. So the pane id is the one reachable self-identifying signal.
_PANE_UUID_RE = re.compile(
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
)


def _own_pane_id() -> tuple[str, bool] | None:
    """Return (pane_id, is_uuid) for THIS process's terminal pane, or None.

    iTerm2: ITERM_SESSION_ID / TERM_SESSION_ID look like ``wNpM:<UUID>`` — the
            embedded UUID is the stable, globally-unique pane id (is_uuid=True).
    tmux:   TMUX_PANE looks like ``%N`` — used verbatim (is_uuid=False).

    Reads BOTH backend families so the resolver works under either; returns None
    when no pane id is reachable (e.g. an in-process frame).
    """
    for var in ("ITERM_SESSION_ID", "TERM_SESSION_ID"):
        match = _PANE_UUID_RE.search(os.environ.get(var, ""))
        if match:
            return (match.group(0), True)
    tmux_pane = os.environ.get("TMUX_PANE", "")
    if tmux_pane:
        return (tmux_pane, False)
    return None


def resolve_lead_team_by_pane(teams_dir: str | None = None) -> tuple[str, str] | None:
    """Resolve a separate-process teammate's LEAD team + its own member name by
    matching this process's terminal pane id against members[].tmuxPaneId across
    the team configs under ``~/.claude/teams/``.

    Returns ``(team_name, own_member_name)`` on a UNIQUE match. Returns None on:
    zero matches, MULTIPLE matches (ambiguous → fail-safe; resolving to the wrong
    team would leak the wrong peer list — never guess), no reachable pane id, or
    any read error. NEVER raises (every read is guarded), so callers can use it
    inside a fail-open hook path.

    Matching is backend-aware: a UUID pane id (iTerm2) uses a SUBSTRING match
    (UUIDs are globally unique, so a substring hit cannot cross teams); a tmux
    pane id uses an EXACT match (a short id like ``%3`` must not substring-
    collide with ``%30``). Members with an empty/missing tmuxPaneId — notably the
    lead — never match.

    Dual-mode: an in-process teammate has no own pane (it shares the lead's
    session), so this returns None and the caller falls back to the existing
    session-derived path — in-process resolution is unaffected.
    """
    try:
        own = _own_pane_id()
        if own is None:
            return None
        pane, is_uuid = own

        if teams_dir is None:
            teams_dir = str(Path.home() / ".claude" / "teams")
        base = Path(teams_dir)
        if not base.is_dir():
            return None

        matches: list[tuple[str, str]] = []
        for config_path in base.glob("*/config.json"):
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # unreadable / non-JSON config → skip, never raise
            if not isinstance(config, dict):
                continue
            members = config.get("members")
            if not isinstance(members, list):
                continue
            for member in members:
                if not isinstance(member, dict):
                    continue
                member_pane = member.get("tmuxPaneId")
                if not isinstance(member_pane, str) or not member_pane:
                    continue  # empty (lead) / missing / non-str → never match
                matched = (pane in member_pane) if is_uuid else (pane == member_pane)
                if matched:
                    name = member.get("name")
                    matches.append(
                        (config_path.parent.name, name if isinstance(name, str) else "")
                    )

        if len(matches) == 1:
            return matches[0]
        return None  # zero or multiple → fail-safe
    except Exception:
        return None  # belt-and-suspenders: the resolver must never raise
