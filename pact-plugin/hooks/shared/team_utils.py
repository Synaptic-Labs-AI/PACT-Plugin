"""
Location: pact-plugin/hooks/shared/team_utils.py
Summary: Shared Agent Teams utility functions for PACT hooks.
Used by: session_init.py, compaction_refresh.py, stop_audit.py

Provides helper functions for deriving team names, checking team existence,
and reading team configuration. These utilities support the per-session
team lifecycle managed by hooks (SessionStart creates, Stop cleans up).

Note: Hooks are shell commands (type: "command" in hooks.json) and cannot
call Claude Code tools (TeamCreate, SendMessage, etc.) directly. These
utilities help hooks generate text instructions for the orchestrator and
inspect on-disk team state.
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Any


def derive_team_name(branch_name: str) -> str:
    """
    Derive a team name from a git branch name.

    Strips common prefixes (feature/, bugfix/, hotfix/) and normalizes
    the remaining path into a safe team name.

    Args:
        branch_name: Git branch name (e.g., "feature/v3-agent-teams")

    Returns:
        Normalized team name (e.g., "v3-agent-teams")

    Examples:
        >>> derive_team_name("feature/v3-agent-teams")
        'v3-agent-teams'
        >>> derive_team_name("bugfix/fix-login")
        'fix-login'
        >>> derive_team_name("main")
        'main'
        >>> derive_team_name("feature/scope/nested-path")
        'scope-nested-path'
    """
    if not branch_name:
        return "pact-session"

    # Strip common branch prefixes
    prefixes = ("feature/", "bugfix/", "hotfix/", "fix/", "chore/", "release/")
    name = branch_name
    for prefix in prefixes:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break

    # Replace path separators and unsafe characters with hyphens
    name = re.sub(r"[/\\._]+", "-", name)

    # Remove leading/trailing hyphens
    name = name.strip("-")

    # Collapse multiple hyphens
    name = re.sub(r"-{2,}", "-", name)

    # Fallback if name ended up empty
    if not name:
        return "pact-session"

    return name


def get_current_branch() -> str:
    """
    Get the current git branch name.

    Returns:
        Branch name string, or empty string if not in a git repo.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass
    return ""


def get_team_config_dir() -> Path:
    """
    Get the base directory for team configuration files.

    Returns:
        Path to ~/.claude/teams/
    """
    return Path.home() / ".claude" / "teams"


def get_team_config_path(team_name: str) -> Path:
    """
    Get the configuration file path for a specific team.

    Args:
        team_name: The team name

    Returns:
        Path to ~/.claude/teams/{team_name}/config.json
    """
    return get_team_config_dir() / team_name / "config.json"


def team_exists(team_name: str) -> bool:
    """
    Check if a team configuration exists on disk.

    Teams are created by the orchestrator via TeamCreate and stored
    in ~/.claude/teams/{team_name}/. This function checks for the
    existence of the team directory.

    Args:
        team_name: The team name to check

    Returns:
        True if team directory exists
    """
    if not team_name:
        return False
    team_dir = get_team_config_dir() / team_name
    return team_dir.is_dir()


def get_team_members(team_name: str) -> list[dict[str, Any]]:
    """
    Read team configuration and return the members list.

    Reads the team config.json if it exists and extracts the members
    array. Each member dict typically contains 'name', 'type', and
    'status' fields.

    Args:
        team_name: The team name

    Returns:
        List of member dicts, or empty list if unavailable
    """
    config_path = get_team_config_path(team_name)
    if not config_path.exists():
        return []

    try:
        content = config_path.read_text(encoding="utf-8")
        config = json.loads(content)
        return config.get("members", [])
    except (IOError, json.JSONDecodeError, KeyError):
        return []


def find_active_teams() -> list[str]:
    """
    Find all team directories under ~/.claude/teams/.

    Returns:
        List of team name strings
    """
    teams_dir = get_team_config_dir()
    if not teams_dir.exists():
        return []

    teams = []
    try:
        for entry in teams_dir.iterdir():
            if entry.is_dir():
                teams.append(entry.name)
    except OSError:
        pass

    return teams
