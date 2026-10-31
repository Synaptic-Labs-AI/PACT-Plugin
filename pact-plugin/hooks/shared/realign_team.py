"""
Location: pact-plugin/hooks/shared/realign_team.py
Summary: The lead-run team realign. Records a named team as this session's
         team (the session context file, the project CLAUDE.md ``- Team:``
         line, and a ``team_realigned`` journal event) after corroborating it
         as this session's, or refuses with exit code 3.
Used by: the lead, from the team-split recovery in state recovery:
         cd "{plugin_root}/hooks" && python3 -m shared.realign_team --team <name>

Exit codes: 0 done (including a team that was already recorded), 2 a usage
error (argparse), 3 a refusal (reason on stderr, nothing written).

Lead only. An in-process teammate runs inside the lead's process, so its
environment is the lead's: $CLAUDE_CODE_SESSION_ID, $CLAUDE_PID and
$CLAUDE_CODE_AGENT are all the lead's values, and no check here can tell the
two apart. The refusal and help texts say so instead.

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
    project_scope,
    session_journal,
    session_resume,
    session_state,
)

EXIT_REFUSED = 3
LEAD_ONLY = "Only the lead runs this command; a teammate never does."


def refusal_reason(team_name: str) -> str:
    """The reason to refuse ``team_name``, or '' to accept it.

    Corroborates the named team as this session's at write time, because
    every later keep (a compaction) trusts the recorded team without
    re-checking it: a lead context must exist for $CLAUDE_CODE_SESSION_ID;
    the name must be one path segment; no team config may name this session
    as its lead unless it is this team; no other PACT session may own the
    team; teams/<name>/config.json must parse; its leadAgentId must end in
    "@<name>" and name a member; and that member's cwd must equal the
    recorded project_dir after os.path.realpath on both sides. The live cwd
    is not an anchor: the command runs from the plugin's hooks dir, which is
    never a lead's cwd.
    """
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    pact_context.init({"session_id": sid})
    context = pact_context.get_pact_context()
    if not sid or context.get("session_id", "") != sid:
        return "no PACT lead context for this session"
    if not session_state.is_safe_path_component(team_name):
        return "unsafe team name"
    own_team = pact_context.identity_matched_team(sid)
    if own_team and own_team.casefold() != team_name.casefold():
        return f"this session leads team {own_team}"
    if pact_context.team_owned_by_other_pact_session(team_name, sid):
        return "another PACT session owns this team"
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


def _write_team_line(session_id: str, team_name: str, session_dir: str,
                     plugin_root: str) -> None:
    """Write the project CLAUDE.md ``- Team:`` line, keeping its Started
    value. Follows the same two preconditions as the per-prompt write-back in
    bootstrap_marker_writer: the file must already exist (update_session_info
    creates one when it is absent), and the journal must already hold a
    session_start (until then bootstrap_prompt_gate owns the block)."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir or not session_dir:
        return
    # The exists() guard and the write below must resolve the SAME target
    # (#1653). On a worktree lead that target is the main checkout, so a guard
    # left on the worktree would silently suppress the write.
    #
    # A DECLARED project dir whose memory root is UNKNOWN must write nothing:
    # `project_memory_root` fails SAFE TO ITS INPUT, which for a writer means
    # planting in the worktree. stderr is this command's problem surface (the
    # exit contract stays 0 -- a skip is not a refusal).
    memory_root = project_scope.project_memory_root_or_none(project_dir)
    if memory_root is None:
        print(
            "realign_team: project CLAUDE.md skipped: git could not answer, so "
            "the project memory target is unknown; nothing was written to "
            "either checkout.",
            file=sys.stderr,
        )
        return
    target_file, _source = claude_md_manager.resolve_project_claude_md_path(
        memory_root
    )
    if (target_file.exists()
            and session_journal.read_last_event_from(
                session_dir, "session_start") is not None):
        session_resume.update_session_info(
            session_id, team_name, session_dir, plugin_root,
            started=session_resume.read_session_started(memory_root),
            project_dir=memory_root,
        )


def realign_team(team_name: str) -> int:
    """Record ``team_name`` as this session's team and return the exit code:
    3 on a refusal (reason on stderr, nothing written), 0 otherwise.

    Writes the context file (keeping its started_at), then the project
    CLAUDE.md ``- Team:`` line, then the ``team_realigned`` event when the
    team changed. Names compare casefolded. A team that is already recorded
    still gets its CLAUDE.md line written, which repairs a line an earlier
    run could not write.
    """
    reason = refusal_reason(team_name)
    if reason:
        print(f"realign refused: {reason}. {LEAD_ONLY}", file=sys.stderr)
        return EXIT_REFUSED
    context = pact_context.get_pact_context()
    previous = context.get("team_name", "")
    session_id = context["session_id"]
    plugin_root = context.get("plugin_root", "")
    unchanged = previous.casefold() == team_name.casefold()
    if unchanged:
        team_name = previous
    else:
        # No process_key: the file keeps the key a lead hook frame recorded.
        # This command's parent is a shell, which has no key of its own.
        pact_context.write_context(
            team_name, session_id, context.get("project_dir", ""), plugin_root,
            started_at=context.get("started_at") or None,
        )
    session_dir = pact_context.get_session_dir()
    _write_team_line(session_id, team_name, session_dir, plugin_root)
    if unchanged:
        print(f"realign: team is already {team_name}")
        return 0
    outcome = session_resume.append_team_realigned(
        previous, team_name, session_dir=session_dir or None
    )
    print(f"realign: team is now {team_name} (was {previous or 'unset'})")
    if outcome == session_resume.REALIGN_NOT_A_SPLIT:
        print(f"realign: {previous or 'the earlier team'} never existed; "
              "there is nothing to carry across")
    elif outcome == session_resume.REALIGN_FAILED:
        print(f"realign: team_realigned was not recorded; the earlier team "
              f"is {previous}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m shared.realign_team",
        description=(
            "Record the team this PACT session belongs to. " + LEAD_ONLY
        ),
        epilog=(
            "exit codes: 0 done, 2 usage error, 3 refused. "
            'example: cd "<plugin_root>/hooks" && '
            "python3 -m shared.realign_team --team session-1a2b3c4d"
        ),
    )
    parser.add_argument("--team", required=True, help="the team directory name")
    args = parser.parse_args(argv)
    return realign_team(args.team)


if __name__ == "__main__":
    sys.exit(main())
