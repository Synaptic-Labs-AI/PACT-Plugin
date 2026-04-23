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
    match_project_claude_md,
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
# Intentional-wait public API surface: only the top-level staleness
# predicate is re-exported. validate_wait, canonical_since,
# KNOWN_REASONS, KNOWN_RESOLVERS, and DEFAULT_THRESHOLD_MINUTES remain
# module-only — `from shared.intentional_wait import X` for those.
# (should_silence_stall_nag + is_signal_task removed in #538 C3 alongside
# detect_stall in teammate_idle.py.)
from .intentional_wait import wait_stale
from .session_state import (
    SAFE_PATH_COMPONENT_RE,
    is_safe_path_component,
)

# Pin caps constants + regex (semantic owner: hooks/pin_caps.py). Re-exported
# here so hook consumers can `from shared import PIN_COUNT_CAP` rather than
# reaching into the sibling module directly. pin_caps lives one directory
# up (in hooks/), a sibling to the shared/ package. Every hook entrypoint
# that imports `shared` already places hooks/ on sys.path (via the plugin
# runner), so `import pin_caps` resolves cleanly at module-load time.
# Skills-side twin copies live in skills/pact-memory/scripts/working_memory.py
# (separate package boundary); the twin-copy drift test compares pin_caps
# directly against working_memory.
from pin_caps import (  # noqa: E402
    PIN_COUNT_CAP,
    PIN_SIZE_CAP,
    PIN_STALE_BLOCK_THRESHOLD,
    OVERRIDE_RATIONALE_MAX,
    OVERRIDE_COMMENT_RE,
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
    "match_project_claude_md",
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
    "wait_stale",
    "SAFE_PATH_COMPONENT_RE",
    "is_safe_path_component",
    "PIN_COUNT_CAP",
    "PIN_SIZE_CAP",
    "PIN_STALE_BLOCK_THRESHOLD",
    "OVERRIDE_RATIONALE_MAX",
    "OVERRIDE_COMMENT_RE",
    "BOOTSTRAP_MARKER_NAME",
    "build_session_path",
    "get_pact_context",
    "get_team_name",
    "get_session_id",
    "get_project_dir",
    "resolve_agent_name",
    "write_context",
]
