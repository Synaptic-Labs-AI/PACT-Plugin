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
- merge_guard_common: Shared constants and cleanup for merge guard hooks
- error_output: Standardized JSON error output for hook exception handlers
- s2_state: Atomic read/write/update for .pact/s2-state.json (S2 coordination state)
"""

from .task_utils import (
    get_task_list,
    find_feature_task,
    find_current_phase,
    find_active_agents,
    find_blockers,
)
from .symlinks import setup_plugin_symlinks
from .claude_md_manager import update_claude_md, ensure_project_memory_md
from .session_resume import (
    update_session_info,
    restore_last_session,
    check_resumption_context,
)
from .merge_guard_common import (
    TOKEN_TTL,
    TOKEN_DIR,
    TOKEN_PREFIX,
    cleanup_consumed_tokens,
)
from .error_output import hook_error_json
from .constants import PACT_AGENTS
from .s2_state import (
    read_s2_state,
    write_s2_state,
    update_s2_state,
    resolve_convention,
    check_boundary_overlap,
    file_in_scope,
)

__all__ = [
    "get_task_list",
    "find_feature_task",
    "find_current_phase",
    "find_active_agents",
    "find_blockers",
    "setup_plugin_symlinks",
    "update_claude_md",
    "ensure_project_memory_md",
    "update_session_info",
    "restore_last_session",
    "check_resumption_context",
    "TOKEN_TTL",
    "TOKEN_DIR",
    "TOKEN_PREFIX",
    "cleanup_consumed_tokens",
    "hook_error_json",
    "PACT_AGENTS",
    "read_s2_state",
    "write_s2_state",
    "update_s2_state",
    "resolve_convention",
    "check_boundary_overlap",
    "file_in_scope",
]
