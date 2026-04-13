"""
Location: pact-plugin/hooks/shared/__init__.py
Summary: Package for shared hook utilities.
Used by: Various PACT hooks that need common Task system integration,
         symlink management, project CLAUDE.md routing block management,
         session resume, and session context resolution.

This package provides shared utilities for hooks:
- pact_context: Session context file reader/writer (team_name, session_id, agent identity)
- task_utils: Task system integration (used by multiple hooks)
- symlinks: Plugin symlink management for @reference resolution
- claude_md_manager: project CLAUDE.md scaffolding (ensure_project_memory_md),
  PACT routing block upsert (update_pact_routing), and one-time migration
  from the legacy ~/.claude/CLAUDE.md kernel block (remove_stale_kernel_block)
- session_resume: Session info, snapshot restore, resumption context
- merge_guard_common: Shared constants and cleanup for merge guard hooks
- error_output: Standardized JSON error output for hook exception handlers
"""

from .task_utils import (
    get_task_list,
    find_feature_task,
    find_current_phase,
    find_active_agents,
    find_blockers,
)
from .symlinks import setup_plugin_symlinks
from .claude_md_manager import (
    file_lock,
    remove_stale_kernel_block,
    update_pact_routing,
    ensure_project_memory_md,
    migrate_to_managed_structure,
    MANAGED_START_MARKER,
    MANAGED_END_MARKER,
    MEMORY_START_MARKER,
    MEMORY_END_MARKER,
    MANAGED_TITLE,
    PACT_BOUNDARY_PREFIXES,
)
from .failure_log import (
    append_failure,
    read_failures,
    LOG_PATH as FAILURE_LOG_PATH,
    MAX_ENTRIES as FAILURE_LOG_MAX_ENTRIES,
)
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
# Convenience re-exports for the public API. Hooks import directly from
# shared.pact_context, but these re-exports allow `from shared import get_team_name`.
from .pact_context import (
    get_pact_context,
    get_team_name,
    get_session_id,
    get_project_dir,
    resolve_agent_name,
    write_context,
)

__all__ = [
    "get_task_list",
    "find_feature_task",
    "find_current_phase",
    "find_active_agents",
    "find_blockers",
    "setup_plugin_symlinks",
    "file_lock",
    "remove_stale_kernel_block",
    "update_pact_routing",
    "ensure_project_memory_md",
    "migrate_to_managed_structure",
    "MANAGED_START_MARKER",
    "MANAGED_END_MARKER",
    "MEMORY_START_MARKER",
    "MEMORY_END_MARKER",
    "MANAGED_TITLE",
    "PACT_BOUNDARY_PREFIXES",
    "append_failure",
    "read_failures",
    "FAILURE_LOG_PATH",
    "FAILURE_LOG_MAX_ENTRIES",
    "update_session_info",
    "restore_last_session",
    "check_resumption_context",
    "TOKEN_TTL",
    "TOKEN_DIR",
    "TOKEN_PREFIX",
    "cleanup_consumed_tokens",
    "hook_error_json",
    "PACT_AGENTS",
    "get_pact_context",
    "get_team_name",
    "get_session_id",
    "get_project_dir",
    "resolve_agent_name",
    "write_context",
]
