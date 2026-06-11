"""
Location: pact-plugin/hooks/shared/constants.py
Summary: Canonical constants shared across PACT hooks and tests.
Used by: test_patterns.py (cross-list consistency checks),
         verify-scope-integrity.sh (baseline checks),
         postcompact_archive.py (get_compact_summary_path),
         session_init.py (get_compact_summary_path).
"""

from __future__ import annotations

from pathlib import Path

from .paths import get_claude_config_dir

# Canonical list of all PACT specialist agents in lifecycle order.
# This is the single source of truth for agent enumeration.
# Keep in sync with: CLAUDE.md agent roster, task_utils.py agent_prefixes,
# refresh/patterns.py PACT_AGENT_PATTERN.
PACT_AGENTS = [
    "pact-preparer",
    "pact-architect",
    "pact-backend-coder",
    "pact-frontend-coder",
    "pact-database-engineer",
    "pact-devops-engineer",
    "pact-n8n",
    "pact-test-engineer",
    "pact-security-engineer",
    "pact-qa-engineer",
    "pact-auditor",
    "pact-secretary",
]

# Canonical path for the compact summary file written by postcompact_archive
# and read by session_init (post-compaction recovery) and pact-secretary
# (session briefing). Single-use: secretary deletes after processing.
# Also referenced in: pact-plugin/agents/pact-secretary.md (documentation only).
# Accessor (B1) — resolves $CLAUDE_CONFIG_DIR at CALL time (no import-time freeze).
def get_compact_summary_path() -> Path:
    return get_claude_config_dir() / "pact-sessions" / "compact-summary.txt"


# Subject prefixes that indicate synthetic / system-level tasks (phase
# markers and algedonic signal tasks) as opposed to real feature work.
# Used by session_state._derive_feature_from_journal and
# _read_feature_subject_from_disk to reject system tasks from the
# feature-subject derivation path.
#
# NOTE: This is distinct from `phase_prefixes` in task_utils.py
# (`PREPARE:`, `ARCHITECT:`, `CODE:`, `TEST:`, `Review:`) — those are
# phase-marker-task-subject prefixes, a narrower set used by
# find_feature_task / find_current_phase. The two tuples have
# different semantics and should not be unified.
SYSTEM_TASK_PREFIXES = ("Phase:", "BLOCKER:", "ALERT:", "HALT:")
