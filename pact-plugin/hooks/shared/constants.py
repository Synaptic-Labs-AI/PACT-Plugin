"""
Location: pact-plugin/hooks/shared/constants.py
Summary: Canonical constants shared across PACT hooks and tests.
Used by: test_patterns.py (cross-list consistency checks),
         verify-scope-integrity.sh (baseline checks),
         postcompact_archive.py (get_compact_summary_path),
         session_init.py (get_compact_summary_path,
                          PIN_STALENESS_MARKER_NAME),
         pin_staleness_gate.py (PIN_STALENESS_MARKER_NAME),
         track_files.py (PIN_STALENESS_MARKER_NAME).
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

# Filename of the compact summary. ONE spelling, shared by the session-scoped
# location ({session_dir}/compact-summary.txt, resolved by
# pact_context.resolve_compact_summary_path) and the root singleton below, so
# neither leg can drift from the name the secretary states in prose.
COMPACT_SUMMARY_NAME = "compact-summary.txt"


# Canonical ROOT path for the compact summary. After #1504 this is the
# DEGRADATION + LEGACY-DRAIN destination, not the primary write target:
# postcompact_archive writes the session-scoped path whenever the frame is
# identifiable and degrades HERE when it is not (missing session_id or
# CLAUDE_PROJECT_DIR), and pre-upgrade bytes start here. session_init drains
# whatever it finds at this path on every non-compact SessionStart.
#
# SINGLE-USE, AND ARCHIVED RATHER THAN DELETED. The file must LEAVE this path
# once processed, because a copy left here is processed again by the next
# briefing in the same session. It does NOT follow that the bytes may go:
# whoever clears it destroys the only copy unless the clear is a MOVE. Both
# clearers move it — the secretary, when its session-scoped read falls back to
# this path, into its session directory; and session_init, into the clearing
# session's directory when it can identify one, else into the orphan slot
# below.
#
# Also referenced in: pact-plugin/agents/pact-secretary.md (the named fallback
# read) and pact-plugin/hooks/session_init.py (the stale-summary archive, and
# the resume-time pointer's degraded branch).
# Accessor (B1) — resolves $CLAUDE_CONFIG_DIR at CALL time (no import-time freeze).
def get_compact_summary_path() -> Path:
    return get_claude_config_dir() / "pact-sessions" / COMPACT_SUMMARY_NAME


# Filename prefix for an ARCHIVED compact summary. One definition, so the hook
# that writes an archive cannot drift from the convention the secretary states
# in prose. The two PROSE statements are pinned against each other by
# tests/test_compact_summary_archive_contract.py; this is the code-side
# spelling, deliberately kept out of that pin's `<placeholder>.txt` shape so
# that defining it here does not change either file's stated-convention count.
COMPACT_SUMMARY_ARCHIVE_PREFIX = "compact-summary-"

# Fixed-name slot for a stale summary that cannot be attributed to a session.
# A FIXED name, never a timestamped one: this lives in the shared sessions root,
# where timestamped files would accumulate without bound. One slot makes that
# growth UNREPRESENTABLE rather than merely policed.
#
# ROLE (narrowed by #1504): the catch basin of the ROOT drain — the destination
# when the CLEARING session is itself unidentified. Since the writer now scopes
# its file to the session that produced it, that means degraded writes and
# pre-upgrade legacy bytes only.
#
# THE TRADE, so nobody discovers it by losing something: a second orphan
# OVERWRITES the first. That is a strict improvement on the previous behaviour,
# which lost the summary every time; and the newer copy is the more likely to be
# wanted, since an older orphan is from a session nobody came back for.
COMPACT_SUMMARY_ORPHAN_NAME = "compact-summary.orphan.txt"

# In the compact directive (session_init) it follows the role marker line and
# precedes the bootstrap instruction, so a teammate reads what it is before it
# reads the instruction the clause tells it to set aside. In the missed-wake
# surface on a compaction (missed_wake_scan) it leads. An in-process teammate's
# compaction arrives lead-shaped and receives both, and its system prompt
# survives the compaction, so the sentence keys on that. It lives here rather
# than in compaction_owner so that a broken settle module cannot disable the scan.
COMPACTION_TEAMMATE_CLAUSE = (
    "If your system prompt makes you a teammate who reports to a team lead, "
    "this message is not for you: ignore it and continue your task."
)


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


# Top-level event-field value marking a `variety_assessed` journal event as a
# PER-DISPATCH mirror (written by the dispatcher's command prose at the Task-B
# stamp site) rather than a feature-level assessment. ABSENT means
# feature-level, so every legacy event is feature-level by construction and
# the position-based consumers (session_state._derive_feature_from_journal /
# _derive_variety_from_journal latest-ts feature-level selection, and
# variety_divergence.resolve_arc_start) exclude this value without any
# migration. Readers compare the TOP-LEVEL event field; the variety DICT
# nested one level down carries a dimension also named "scope" (one of the
# four variety dimensions), and the two never interact.
VARIETY_ASSESSED_DISPATCH_SCOPE = "dispatch"


# Marker file name written when the stale-pins-pending state is detected.
# Placed in session_dir so that it is per-session scoped. It clears on a new
# session, and it cannot persist across /clear, because session_dir is rebuilt
# for each session.
#
# WHY THE NAME LIVES HERE AND NOT IN THE GATE THAT ENFORCES IT. Three modules
# read this name and they run in three different frames: pin_staleness_gate.py
# (PreToolUse), track_files.py (PostToolUse) and session_init.py (SessionStart).
# The gate wraps its own cross-package imports in a FAIL-CLOSED handler that
# prints a PreToolUse deny and calls `sys.exit(2)`. That posture is correct for
# a PreToolUse frame and incorrect for the other two. `SystemExit` derives from
# `BaseException`, so an `except Exception` in a caller does NOT contain it, and
# a PostToolUse hook that reads the name through the gate inherits an exit that
# emits a deny for an event nobody can deny, and drops the work that the hook
# was registered to do. THIS MODULE HAS NO EXIT PATH, so a reader takes the
# name without taking that risk. DO NOT move this constant into a gate module,
# and do not repair a future instance of this coupling by widening an exception
# handler: the handler catches the symptom and leaves the dependency in place.
#
# WHERE THE LIFECYCLE LIVES, AND IT IS TWO SURFACES RATHER THAN ONE.
# `session_init.check_pin_stale_block_directive` writes the marker and removes
# it, at SessionStart. `track_files.clear_pin_staleness_marker_if_resolved`
# removes it MID-SESSION as well, on a hand edit to the managed file and on the
# archive command, and only after it re-reads the signal and finds the
# condition clear.
#
# A NAME COLLISION THAT TRAPS A SEARCH, RECORDED BECAUSE SOMEBODY CHECKED IT
# RATHER THAN ASSUMED IT. `pin_marker_writer.py` is registered on
# `UserPromptSubmit` AND on `PostToolUse` with the matcher `Skill`, so it READS
# like a mid-session manager of this signal. IT IS NOT ONE. It carries zero
# references to this constant, and it serves the pin command, whose
# `## Pinned Context` marker pair is a different object that shares a naming
# family. A reader who greps for the hook that manages this marker finds that
# file first, and it is the incorrect file.
PIN_STALENESS_MARKER_NAME = "pin-staleness-pending"
