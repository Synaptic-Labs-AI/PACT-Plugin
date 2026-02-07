"""
Location: pact-plugin/hooks/shared/__init__.py
Summary: Package for shared hook utilities.
Used by: Various PACT hooks that need common Task and Team system integration.

This package provides shared utilities for hooks:
- Task system integration (reading task state from filesystem)
- Agent Teams utilities (team name derivation, team state inspection)
"""

from .task_utils import (
    get_task_list,
    find_feature_task,
    find_current_phase,
    find_active_agents,
    find_blockers,
)

from .team_utils import (
    derive_team_name,
    get_current_branch,
    get_team_config_path,
    team_exists,
    get_team_members,
    find_active_teams,
)

__all__ = [
    # Task utilities
    "get_task_list",
    "find_feature_task",
    "find_current_phase",
    "find_active_agents",
    "find_blockers",
    # Team utilities
    "derive_team_name",
    "get_current_branch",
    "get_team_config_path",
    "team_exists",
    "get_team_members",
    "find_active_teams",
]
