#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/stale_session.py
Summary: Shared detector for a stale 'Current Session' block in the project
         CLAUDE.md — compares this frame's live stdin session_id against the
         CLAUDE.md Resume-line id and returns a diagnostic warning on mismatch.
Used by: bootstrap_prompt_gate.py (UserPromptSubmit injection) and
         dispatch_gate.py (deny-message self-diagnosis at the two team/task
         deny sites). Single SSOT for THIS specific signal — the
         CLAUDE.md-Resume-line-recorded vs live-stdin session_id mismatch — so
         both consumers read one implementation. (Scoped deliberately: this is
         NOT the only restart-detection signal; a context-SSOT-keyed sibling is
         planned — see the breadcrumb below the constants.)

The detector is the reuse seam for the restart/persistence reliability work:
after a full Claude Code restart/fork the platform mints a new session-<id8>
team while PACT's persisted session_id/team_name go stale, so the live stdin
session_id stops matching the CLAUDE.md-recorded one. This module names that
mismatch once; consumers surface it in their own channel (additionalContext
for bootstrap_prompt_gate, the deny message for dispatch_gate).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import shared.pact_context as pact_context

# Mirrors the Resume-line fallback regex in session_init's
# _extract_prev_session_dir — the established defensive parse for the
# session_resume.update_session_info managed block. Parity with
# claude_md_manager.resolve_project_claude_md_path's existing-file
# precedence is pinned by test.
_RESUME_LINE_RE = re.compile(r"- Resume:\s*`claude --resume\s+([0-9a-f-]+)`")

_STALENESS_WARNING_TEMPLATE = (
    "\n\nWARNING — stale session block: the project CLAUDE.md 'Current "
    "Session' block records session {recorded} but this session is "
    "{actual}. session_init likely failed at SessionStart this session "
    "(or the CLAUDE.md write failed). Do NOT trust the recorded Team/"
    "Session dir/Resume lines for THIS session; completing bootstrap "
    "will rewrite them."
)

# DECIDED — `is_session_restarted` (below) is a SEPARATE SIBLING of
# `detect_stale_session_block`, NOT a shared/generalized accessor. The two
# read DIFFERENT trust sources that can legitimately disagree after a restart:
# `detect_stale_session_block` keys on the CLAUDE.md Resume-line id, while
# `is_session_restarted` keys on the PERSISTED CONTEXT SSOT
# (pact-session-context.json session_id) vs the live stdin id. After a restart
# these can diverge (e.g. session_init wrote a fresh context but the CLAUDE.md
# write failed, or vice versa), and each consumer needs the signal from ITS
# OWN trust source: the dispatch-deny diagnosis is about the CLAUDE.md-recorded
# vs live mismatch (what the user is told to fix); the reconciliation trigger
# is about the context-SSOT vs live mismatch (what the write-back repairs).
# Forcing them through a shared accessor would couple two independent signals
# and require picking one source as canonical, defeating the point that they
# legitimately differ. Kept as siblings in the same leaf by design.


def detect_stale_session_block(input_data: dict) -> str | None:
    """Detect a stale 'Current Session' block in the project CLAUDE.md.

    When session_init crashes at SessionStart, the previous session's
    Resume/Team/Session-dir lines survive in CLAUDE.md and misdirect
    recovery. Compare the recorded Resume-line session_id against this
    frame's raw stdin session_id; on mismatch, return an advisory warning
    string for additionalContext composition. Returns None (no warning)
    when:

      1. stdin session_id is missing or invalid per the canonical
         _is_unknown_or_missing_session predicate (None/non-string/empty/
         whitespace-only/`unknown-*` sentinel/C0-control chars) — nothing
         trustworthy to compare, and an unvalidated stdin id must never be
         interpolated into the warning text
      2. CLAUDE_PROJECT_DIR is unset (cannot locate CLAUDE.md)
      3. neither CLAUDE.md location exists, or reading raises OSError or
         UnicodeDecodeError (worktrees: CLAUDE.md is gitignored/absent →
         silent skip; a non-UTF-8/corrupted CLAUDE.md → silent skip — this
         helper is ADVISORY, so its failure budget is "no warning", never
         "no bootstrap instruction": an uncaught raise here would propagate
         to a consumer's fail-open and suppress the ENTIRE injection, primary
         instruction included)
      4. no Resume line matches the regex (tampered/garbage → no claim)
      5. recorded session_id equals this session's (healthy resume)

    Stdlib-only two-path read: .claude/CLAUDE.md preferred, legacy
    ./CLAUDE.md fallback — same existing-file precedence as
    resolve_project_claude_md_path (parity pinned by test). False
    positives: none in healthy flows — session_init rewrites the block
    before the first prompt on startup/clear, and resume keeps the same
    session_id.
    """
    raw_id = input_data.get("session_id")
    # Canonical validity predicate (shared with the heal gate and
    # session_init's persist/CLAUDE.md-write gates) — subsumes the plain
    # truthiness check and additionally rejects non-string, whitespace-only,
    # `unknown-*` sentinel, and C0-control-char ids, so `actual` below is
    # never an attacker-shaped or sentinel value interpolated into the
    # warning. The predicate is total for any input.
    if pact_context._is_unknown_or_missing_session(raw_id):
        return None
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None
    try:
        content = None
        for candidate in (
            Path(project_dir) / ".claude" / "CLAUDE.md",
            Path(project_dir) / "CLAUDE.md",
        ):
            if candidate.exists():
                content = candidate.read_text(encoding="utf-8")
                break
        if content is None:
            return None
    except (OSError, UnicodeDecodeError):
        # UnicodeDecodeError (a ValueError, NOT an OSError) from read_text
        # on a non-UTF-8 CLAUDE.md — e.g. a latin-1 byte from a wrong-editor
        # save, or the partial/corrupted session_init write this detector
        # exists to flag. Must be swallowed HERE: this helper composes into
        # the load-bearing bootstrap instruction by concatenation, and an
        # escape to a consumer's fail-open suppresses the whole injection.
        return None
    match = _RESUME_LINE_RE.search(content)
    if not match:
        return None
    recorded = match.group(1)
    actual = str(raw_id)
    if recorded != actual:
        # Render-bound length cap (defense-in-depth, message-only). The
        # Resume-line capture is `[0-9a-f-]+` (UNBOUNDED), so a tampered
        # CLAUDE.md with a 200k-hex id would interpolate a 200k-char warning
        # into the consumer's surface (a dispatch deny's permissionDecisionReason
        # / a UserPromptSubmit additionalContext). Cap BOTH AFTER the mismatch
        # comparison (comparing the FULL values, so two long-but-distinct ids
        # cannot collide on a shared 64-char prefix and wrongly suppress a real
        # mismatch). 64 is non-lossy for a 36-char session UUID and needs no
        # extra import (this leaf is deliberately stdlib-only); both fields are
        # already control-char-free (recorded is hex-only; actual passed
        # _is_unknown_or_missing_session), so the length bound is the only need.
        return _STALENESS_WARNING_TEMPLATE.format(
            recorded=recorded[:64], actual=actual[:64]
        )
    return None


def is_session_restarted(input_data: dict) -> bool:
    """Return True iff the live stdin session_id differs from the PERSISTED
    CONTEXT session_id (both present and valid) — the restart/fork structural
    signal, keyed on the context SSOT (the sibling of
    detect_stale_session_block, which keys on the CLAUDE.md Resume-line; see
    the DECIDED note above the constants).

    PURE: reads input_data + get_pact_context(); no writes, NEVER raises.

    The structural discriminator is ``S_live != S_persisted``:

      S_live      = input_data['session_id']            (this running process)
      S_persisted = get_pact_context()['session_id']    (the persisted SSOT)

    Returns False (NOT a restart) when:

      1. S_live is missing or invalid per the canonical
         _is_unknown_or_missing_session predicate (None / non-string / blank /
         `unknown-*` sentinel / C0-control) — nothing trustworthy to compare,
         so a malformed stdin id must never spuriously trip reconciliation.
      2. S_persisted is empty — a fresh first-ever session (empty persisted
         context) has no prior identity to reconcile from. This dovetails with
         the empty-SSOT fail-closed gate (#989/#992): no reconciliation is
         attempted when there is nothing to reconcile.
      3. S_live == S_persisted — the healthy steady state (plain --resume
         preserves the id; a compact/clear re-fires with the same id).

    Mode-agnostic structural discriminator: the signal compares live-vs-
    persisted, NEVER ``leadSessionId == session_id`` (which is True in BOTH the
    in-process and tmux topologies and so is not a restart discriminator). It
    is therefore True on a genuine restart in either topology.

    Path-safety is irrelevant to this predicate: S_live and S_persisted are
    string-compared only, never composed into a Path. Path-safety applies
    downstream, to the matched DIR NAME at _resolve_aligned_team_name's join.
    """
    raw_id = input_data.get("session_id")
    # Validity gate via the single canonical malformed-stdin predicate so an
    # untrustworthy live id can never spuriously trip reconciliation.
    if pact_context._is_unknown_or_missing_session(raw_id):
        return False
    s_persisted = pact_context.get_pact_context().get("session_id", "")
    if not s_persisted:
        return False
    return str(raw_id) != s_persisted
