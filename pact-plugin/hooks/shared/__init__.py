"""
Location: pact-plugin/hooks/shared/__init__.py
Summary: Package for shared hook utilities.
Used by: Various PACT hooks that need common Task system integration,
         symlink management, CLAUDE.md manipulation, and session resume.

This package provides shared utilities for hooks:
- task_utils: Task system integration (used by multiple hooks)
- symlinks: Plugin symlink management for @reference resolution
- claude_md_manager: CLAUDE.md file creation and update
- session_resume: Session info, snapshot restore, resumption context
"""

from .task_utils import (
    get_task_list,
    find_feature_task,
    find_current_phase,
    find_active_agents,
    find_blockers,
)

__all__ = [
    "get_task_list",
    "find_feature_task",
    "find_current_phase",
    "find_active_agents",
    "find_blockers",
]
