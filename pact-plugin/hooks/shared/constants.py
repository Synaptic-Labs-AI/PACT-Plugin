"""
Location: pact-plugin/hooks/shared/constants.py
Summary: Canonical constants shared across PACT hooks and tests.
Used by: test_patterns.py (cross-list consistency checks),
         verify-scope-integrity.sh (baseline checks),
         postcompact_verify.py (COMPACT_SUMMARY_PATH),
         session_init.py (COMPACT_SUMMARY_PATH).
"""
from pathlib import Path

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

# Canonical path for the compact summary file written by postcompact_verify
# and read by session_init (post-compaction recovery) and pact-secretary
# (session briefing). Single-use: secretary deletes after processing.
# Also referenced in: pact-plugin/agents/pact-secretary.md (documentation only).
COMPACT_SUMMARY_PATH = Path.home() / ".claude" / "pact-sessions" / "compact-summary.txt"
