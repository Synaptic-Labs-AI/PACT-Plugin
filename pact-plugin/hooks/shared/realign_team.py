"""
Location: pact-plugin/hooks/shared/realign_team.py
Summary: The lead-run team realign. Records a named team as this session's
         team (the session context file, the project CLAUDE.md ``- Team:``
         line, and a ``team_realigned`` journal event) after corroborating it
         as this session's, or refuses with exit code 2.
Used by: the lead, from the team-split step in state recovery:
         cd "{plugin_root}/hooks" && python3 -m shared.realign_team --team <name>

No hook imports this module, and it must stay that way: it imports
claude_md_manager, session_journal and session_resume, and any module that
imported it would carry those into its import closure. It is also why the
command is not a ``__main__`` block in pact_context: shared/__init__.py
imports pact_context, so ``python3 -m shared.pact_context`` would run a
second copy of an already-imported module.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Module imports, not names: a test that patches a function on its source
# module (pact_context.write_context, session_journal.append_event, ...)
# must reach this command too.
from shared import (
    claude_md_manager,
    pact_context,
    paths,
    session_journal,
    session_resume,
    session_state,
)


def refusal_reason(team_name: str) -> str:
    """The reason to refuse ``team_name``, or '' to accept it.

    Corroborates the named team as this session's at write time, because
    every later keep (a compaction) trusts the recorded team without
    re-checking it: a lead context must exist for $CLAUDE_CODE_SESSION_ID
    (a session check: an in-process teammate shares that id and passes it
    too); the name must be one path segment; teams/<name>/config.json must
    parse; its leadAgentId must end in "@<name>" and name a member; and that
    member's cwd must equal the recorded project_dir after os.path.realpath
    on both sides. The live cwd is not an anchor: the command runs from the
    plugin's hooks dir, which is never a lead's cwd.
    """
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    pact_context.init({"session_id": sid})
    context = pact_context.get_pact_context()
    if not sid or context.get("session_id", "") != sid:
        return "no PACT lead context for this session"
    if not session_state.is_safe_path_component(team_name):
        return "unsafe team name"
    try:
        config = json.loads(
            (paths.get_claude_config_dir() / "teams" / team_name / "config.json")
            .read_text(encoding="utf-8")
        )
        lead_agent_id = str(config.get("leadAgentId", ""))
        members = config.get("members") or []
    except (OSError, ValueError, AttributeError, TypeError):
        return "no config for team"
    if not isinstance(members, list):
        return "team config has no lead member for this team"
    lead_entry = None
    if lead_agent_id.endswith(f"@{team_name}"):
        lead_entry = next(
            (m for m in members
             if isinstance(m, dict) and str(m.get("agentId", "")) == lead_agent_id),
            None,
        )
    if lead_entry is None:
        return "team config has no lead member for this team"
    lead_cwd = str(lead_entry.get("cwd", ""))
    project_dir = context.get("project_dir", "")
    if (not lead_cwd or not project_dir
            or os.path.realpath(lead_cwd) != os.path.realpath(project_dir)):
        return "team lead cwd does not match this session"
    return ""


def realign_team(team_name: str) -> int:
    """Record ``team_name`` as this session's team and return the exit code:
    2 on a refusal (reason on stderr, nothing written), 0 otherwise.

    Writes the context file, then the project CLAUDE.md ``- Team:`` line,
    then appends ``team_realigned`` when the team changed. The CLAUDE.md
    write follows the same two preconditions as the per-prompt write-back in
    bootstrap_marker_writer: the file must already exist (update_session_info
    creates one when it is absent), and the journal must already hold a
    session_start (until then bootstrap_prompt_gate owns the block).
    """
    reason = refusal_reason(team_name)
    if reason:
        print(f"realign refused: {reason}", file=sys.stderr)
        return 2
    context = pact_context.get_pact_context()
    previous = context.get("team_name", "")
    if previous == team_name:
        print(f"realign: team is already {team_name}")
        return 0
    session_id = context["session_id"]
    plugin_root = context.get("plugin_root", "")
    pact_context.write_context(
        team_name, session_id, context.get("project_dir", ""), plugin_root
    )
    session_dir = pact_context.get_session_dir()

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if project_dir and session_dir:
        target_file, _source = claude_md_manager.resolve_project_claude_md_path(
            project_dir
        )
        if (target_file.exists()
                and session_journal.read_last_event_from(
                    session_dir, "session_start") is not None):
            session_resume.update_session_info(
                session_id, team_name, session_dir, plugin_root
            )
    if not session_journal.append_event(
        session_journal.make_event(
            "team_realigned", **{"from": previous, "to": team_name}
        ),
        session_dir=session_dir or None,
    ):
        print("realign: team_realigned journal event was not recorded", file=sys.stderr)
    print(f"realign: team is now {team_name} (was {previous or 'unset'})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m shared.realign_team",
        description="Record the team this PACT session belongs to.",
        epilog=(
            'example: cd "<plugin_root>/hooks" && '
            "python3 -m shared.realign_team --team session-1a2b3c4d"
        ),
    )
    parser.add_argument("--team", required=True, help="the team directory name")
    args = parser.parse_args(argv)
    return realign_team(args.team)


if __name__ == "__main__":
    sys.exit(main())
