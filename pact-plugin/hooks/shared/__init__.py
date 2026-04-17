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
- gh_helpers: Shared gh CLI wrappers (fail-open by construction)
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
    extract_managed_region,
    MANAGED_START_MARKER,
    MANAGED_END_MARKER,
    MEMORY_START_MARKER,
    MEMORY_END_MARKER,
    MANAGED_TITLE,
    PACT_BOUNDARY_PREFIXES,
    PACT_ROUTING_BLOCK,
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
from .gh_helpers import check_pr_state
from .constants import PACT_AGENTS, SYSTEM_TASK_PREFIXES
from .session_state import (
    SAFE_PATH_COMPONENT_RE,
    is_safe_path_component,
)

# Bootstrap gate marker — the session-scoped file whose presence signals that
# Skill("PACT:bootstrap") has been invoked and the tool gate can self-disable.
# Used by bootstrap_gate.py, bootstrap_prompt_gate.py, and session_init.py.
# Also referenced (as a string literal) in commands/bootstrap.md.
BOOTSTRAP_MARKER_NAME = "bootstrap-complete"
# Convenience re-exports for the public API. Hooks import directly from
# shared.pact_context, but these re-exports allow `from shared import get_team_name`.
from .pact_context import (
    _build_session_path as build_session_path,
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
    "extract_managed_region",
    "MANAGED_START_MARKER",
    "MANAGED_END_MARKER",
    "MEMORY_START_MARKER",
    "MEMORY_END_MARKER",
    "MANAGED_TITLE",
    "PACT_BOUNDARY_PREFIXES",
    "PACT_ROUTING_BLOCK",
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
    "check_pr_state",
    "PACT_AGENTS",
    "SYSTEM_TASK_PREFIXES",
    "SAFE_PATH_COMPONENT_RE",
    "is_safe_path_component",
    "BOOTSTRAP_MARKER_NAME",
    "build_session_path",
    "get_pact_context",
    "get_team_name",
    "get_session_id",
    "get_project_dir",
    "resolve_agent_name",
    "write_context",
]
