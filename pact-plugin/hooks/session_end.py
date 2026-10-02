#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/session_end.py
Summary: SessionEnd hook that writes a session_end journal event and performs
         session directory cleanup.
Used by: hooks.json SessionEnd hook

Actions:
1. Write session_end event to the session journal
2. Detect open PRs that were not paused (append warning to journal)
3. Clean up stale session directories using a dual TTL (30 days active, 180 days paused/refreshed)

Purely observational — no destructive operations on project files. Session
directory cleanup is best-effort and never blocks session termination.

Input: JSON from stdin with session context
Output: None (SessionEnd hooks cannot inject context)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import sys
import time
from pathlib import Path

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

from shared.error_output import hook_error_json
from shared import check_pr_state
import shared.pact_context as pact_context
from shared.pact_context import (
    _UNSAFE_SLUG_CHARS_RE,
    get_project_dir,
    get_session_id,
    get_team_name,
    project_slug,
)
from shared.session_journal import (
    append_event,
    make_event,
    read_events,
    read_last_event_from,
)

from shared.session_state import is_safe_path_component
from shared.session_registry import get_registry_path as _get_registry_path
from shared.paths import get_claude_config_dir
from shared.task_utils import get_task_list

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def get_project_slug() -> str:
    """Derive project slug from session context (resolved basename of
    project_dir, via the shared derivation every session path uses)."""
    return project_slug(get_project_dir())


def check_unpaused_pr(
    tasks: list[dict] | None,
    project_slug: str,
) -> str | None:
    """
    Safety-net: detect open PRs that were NOT paused (no memory consolidation).

    Compares the session journal's most-recent `session_paused` event against
    its most-recent `review_dispatch` event. The pause covers a PR only when
    it occurred at-or-after that PR was dispatched; an older pause does NOT
    cover a freshly-dispatched PR (e.g., pause→resume→new PR→quit). If the
    current PR is unpaused, returns a warning string so the caller can attach
    it to the single `session_end` journal event.

    Also checks task metadata as fallback for PRs not tracked through the normal
    review workflow (preserves the existing safety-net regex detection).

    This is detection-only. SessionEnd is async fire-and-forget and cannot run
    agents or memory operations.

    Args:
        tasks: List of task dicts from get_task_list(), or None
        project_slug: Project identifier for the session directory

    Returns:
        Warning string if an unpaused PR is detected, otherwise None.
    """
    if not project_slug:
        return None

    # Fix B (#453): structural consolidation signal — short-circuit if
    # /PACT:wrap-up or /PACT:pause ran Pass 2 memory consolidation in
    # this session. Placed first because it is the cheapest check
    # (disk-local journal read already cached by read_events) and
    # covers the most common false-positive cases (wrap-up on merged
    # PR, pause with consolidation). Fail-open: read_events returns []
    # on missing journal / unreadable journal / corrupt entries, which
    # falls through to the legacy logic below — identical to pre-fix
    # behavior for sessions that never consolidated.
    if read_events("session_consolidated"):
        return None

    paused_events = read_events("session_paused")
    review_events = read_events("review_dispatch")

    # Reconcile pause vs review timing: a pause only "covers" a PR when it
    # occurred at-or-after that PR's dispatch. Bias toward "paused" (silence)
    # on equal timestamps via `>=` to avoid spurious warnings on the
    # 1-second ISO precision tie.
    if paused_events and review_events:
        last_pause_ts = paused_events[-1].get("ts", "")
        last_review_ts = review_events[-1].get("ts", "")
        if last_pause_ts >= last_review_ts:
            return None  # Most recent PR was paused; safe.
        # else fall through — current PR is unpaused
    elif paused_events:
        return None  # Paused, no PRs at all — safe.

    # Check journal for PR creation
    pr_number = None
    if review_events:
        # Use the most recent review_dispatch event's PR number
        pr_number = review_events[-1].get("pr_number")

    # Fallback: scan task metadata for PR indicators (safety net for PRs
    # not tracked through the review workflow journal events)
    if not pr_number and tasks:
        for task in tasks:
            metadata = task.get("metadata") or {}
            if metadata.get("pr_number") is not None:
                pr_number = metadata["pr_number"]
                break
            handoff = metadata.get("handoff") or {}
            for value in handoff.values():
                if isinstance(value, str):
                    match = re.search(r'github\.com/[^/]+/[^/]+/pull/(\d+)', value)
                    if match:
                        pr_number = match.group(1)
                        break
            if pr_number:
                break

    if not pr_number:
        return None

    # Fix A (#453): live PR-state check — last-line-of-defense against
    # merged or closed PRs that neither Fix B nor the pause-vs-review
    # timestamp comparison caught (e.g., PR merged via GitHub web UI
    # mid-session with no wrap-up). Invoked only when every cheaper
    # signal has fallen through, so AC#4 (no network for wrap-up cases)
    # is preserved structurally by the ordering above.
    #
    # Fail-open: check_pr_state returns "" on gh-missing / timeout /
    # auth-expired / OSError. "" is not in ("MERGED", "CLOSED"), so we
    # fall through to the warning — the conservative pre-fix behavior
    # when we cannot distinguish "offline" from "PR actually open."
    pr_state = check_pr_state(pr_number)
    if pr_state in ("MERGED", "CLOSED"):
        return None

    return (
        f"Session ended without memory consolidation. "
        f"PR #{pr_number} may still be open but pause-mode was not run. "
        f"Run /PACT:pause or /PACT:wrap-up in next session."
    )


# Regex for validating UUID-format directory names (session IDs).
# `\Z` (strict end-of-string) is used instead of `$`: in Python `re`,
# `$` matches end-of-string OR immediately before a trailing newline,
# so `deadbeef-dead-beef-dead-beefdeadbeef\n` would pass a `$` anchor
# and re-enter the skip-set / reaper allowlist as a crafted name.
# `\Z` rejects trailing newlines and is the stricter anchor.
_UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z'
)

# Regex gating which team directory names the teams-reaper may consider
# reaping. generate_team_name was migrated to emit `session-<id8>`
# (shared/pact_context.py); this reaper DELIBERATELY stays `^pact-`-scoped
# — its historical namespace — because the platform owns teardown of its
# own `session-*` namespace, so widening toward `session-*` would arm
# `shutil.rmtree` against live platform-owned dirs. Within the `pact-`
# namespace the regex accepts any `pact-`-prefixed lowercase-hex-and-hyphen
# shape to tolerate drift (e.g. a future naming scheme that introduces
# internal hyphens) without silently reaping a live team dir.
# Non-matching entries in ~/.claude/teams/ belong to other tooling and
# MUST NOT be reaped by cleanup_old_teams, even if they're stale by
# mtime. The reaper treats ~/.claude/teams/ as shared space, not
# PACT-owned space. `\Z` (strict end-of-string) — see _UUID_PATTERN.
_TEAM_NAME_PATTERN = re.compile(r'^pact-[a-f0-9-]+\Z')

# Default threshold for active (neither paused nor refreshed) session
# directory cleanup.
# 30 days balances disk usage (~50KB × 30 sessions = ~1.5MB) against
# cross-session recovery value.
_SESSION_MAX_AGE_DAYS = 30

# Extended threshold for checkpointed (paused or refreshed) session
# directories. A checkpoint marks in-progress work the user means to resume,
# so it gets a longer TTL than active sessions to protect the pause→resume
# and refresh→resume workflows across long gaps. The extended TTL is
# protection, not permanent retention — checkpointed sessions still age out
# past this threshold.
_PAUSED_SESSION_MAX_AGE_DAYS = 180


def _is_paused_session(session_dir: str) -> bool:
    """
    Return True iff this session has ever recorded a session_paused event.

    This is a pure "has-ever-been-paused" existence predicate — it does NOT
    compare timestamps against session_end events. A session that was paused
    and later ended still counts as paused from the cleanup policy's
    perspective; the caller (`cleanup_old_sessions`) then applies the
    extended paused TTL (`_PAUSED_SESSION_MAX_AGE_DAYS`, default 180 days)
    to such sessions.

    Splitting the predicate from the policy closes two data-loss bugs that
    existed in the older timestamp-comparison form:

    - AdvF1 (pause→quit race): `/PACT:pause` writes `session_paused`, then
      quitting Claude Code fires `session_end` ~1s later. Any ordering where
      `session_end.ts >= session_paused.ts` used to return False and delete
      the paused state at the 30-day TTL.
    - BugF2 (equal-timestamp tie): journal timestamps have 1-Hz ISO
      precision, so pause and end events landing in the same wall-clock
      second produced equal `ts` fields and hit the old `>=` comparison.

    By dropping the timestamp comparison entirely, neither race nor tie can
    produce a wrong answer.

    Fail-open: if the journal is missing, empty, or unreadable,
    `read_last_event_from` returns None and this predicate returns False so
    the caller is free to apply the standard active-session TTL.

    Args:
        session_dir: Absolute path to the session directory.

    Returns:
        True iff a `session_paused` event exists in the session's journal.
    """
    return read_last_event_from(session_dir, "session_paused") is not None


def _is_checkpointed_session(session_dir: str) -> bool:
    """True iff the session ever recorded a session_paused OR session_refreshed
    event. Pure existence — NO ts comparison (the pause→quit race precedent:
    ts-ordering against session_end deletes state the user meant to keep;
    the same race exists verbatim for refresh→quit, where session_end fires
    ~1s after the session_refreshed write).

    Extends the `_is_paused_session` policy predicate to refresh checkpoints
    so `cleanup_old_sessions` applies the extended paused TTL
    (`_PAUSED_SESSION_MAX_AGE_DAYS`, default 180 days) to a
    refreshed-but-never-resumed journal. This guard protects the JOURNAL
    only: the tasks/teams stores keep their own 30-day reapers (which never
    read journals), so past 30 days a refresh resume degrades to
    journal-only fidelity — the resume prompt's HALT cross-check against
    live tasks then surfaces its loud mismatch warning by design.

    Fail-open like `_is_paused_session`: unreadable journal ⇒ False ⇒
    standard active-session TTL.

    Args:
        session_dir: Absolute path to the session directory.

    Returns:
        True iff a `session_paused` or `session_refreshed` event exists.
    """
    # Composed on _is_paused_session (not inlined) so there is exactly ONE
    # paused-existence predicate — the two can never drift.
    return (_is_paused_session(session_dir)
            or read_last_event_from(session_dir, "session_refreshed") is not None)


def _journal_carries_unharvested_handoffs(session_dir: str) -> bool | None:
    """
    Tri-state carrier test on one session journal.

    This does NOT call `read_last_event_from`. That helper ends in a bare
    `except Exception: return None`, so "the event is absent" and "the
    journal could not be read" give it the same value, and a caller cannot
    separate them. A guard built on it removes the directory on an
    unreadable journal, which is the fail-open direction this predicate
    closes. This function opens the file itself, so a read failure reaches
    its own `except` and becomes the third state.

    The event name comes from the `type` field, because that is where the
    journal writer puts it. A read keyed on `event` matches no line and
    gives a confident absence rather than an error.

    This catches `OSError` only. A bare `except Exception` can swallow a
    programming error and give the refuse value, which keeps the bytes and
    hides the defect. The caller carries the never-raises contract.

    `session_consolidated` RESETS the flag. It does not end the scan. A
    refresh emits that event mid-journal and the workstream CONTINUES in
    the same session and the same file, so a handoff written after it is
    un-harvested. The question is POSITIONAL: is a handoff present after
    the last consolidation, and not merely present somewhere.

    A torn line makes the answer UN-EVALUABLE rather than absent, but only
    when one other line of the file parses as an event. A file of which no
    line parses is not a journal this predicate can read, and it keeps the
    False answer that the shipped reaper depends on.

    Args:
        session_dir: Absolute path to the session directory.

    Returns:
        True: an `agent_handoff` event is present after the last
            `session_consolidated` event, or with no such event in the
            file. The knowledge reached no durable second carrier.
        False: no such handoff is present, OR the journal file is absent,
            OR no line in the file parses as an event.
            KNOWN BOUND, and it is a decision rather than an oversight: a
            file of which the ONLY line is torn answers False, and the
            caller REMOVES it, because `saw_event` stays False. That is
            the degenerate end of the crashed-session shape this guard
            protects. The alternative refuses for each file that is not a
            journal, which retains any non-journal file forever, so this
            bound is accepted.
        None: the read raised, OR the file holds a torn line together with
            at least one well-formed event. The carrier question is
            un-evaluable, and the caller must refuse.
    """
    journal = Path(session_dir) / "session-journal.jsonl"
    try:
        if not journal.exists():
            return False
        has_handoff = False
        saw_event = False
        saw_torn_line = False
        # `utf-8-sig` strips a leading byte-order mark. Decoded as plain
        # utf-8 the mark reaches `json.loads` on the first line and makes
        # THAT LINE unparseable. The outcome then splits. If one other line
        # of the file parses, the answer is None and the caller refuses. If
        # no other line parses, the answer is False and the caller REMOVES
        # a journal that holds a handoff. The two answers are incorrect and
        # one of them erases.
        with journal.open(encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                # Parse each line. DO NOT add a substring pre-filter on the
                # raw line as a speed measure. A `type` value written with a
                # JSON escape sequence parses to the correct name and does
                # NOT contain the literal text, so such a filter drops the
                # line and the verdict reads as if the event were absent,
                # which removes the directory. The verdict keys on the
                # PARSED `type` field and on nothing else.
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    # A torn line is NOT an absent event. The writer takes
                    # an exclusive lock and does no fsync, so a crash mid
                    # write leaves a partial line, and that is the crashed
                    # session this guard exists for. Record it and decide
                    # at the end of the file.
                    if line.strip():
                        saw_torn_line = True
                    continue
                if not isinstance(event, dict):
                    continue
                saw_event = True
                event_type = event.get("type")
                if event_type == "session_consolidated":
                    # RESET, and NOT an early return. A refresh emits this
                    # mid-journal and the workstream continues in the same
                    # file, so a handoff below this line is un-harvested.
                    # Clear the flag and let the rest of the file decide.
                    has_handoff = False
                elif event_type == "agent_handoff":
                    has_handoff = True
        if has_handoff:
            return True
        if saw_torn_line and saw_event:
            # The file is a journal AND one line of it is unreadable, so
            # the carrier question cannot be answered. Refuse. A file of
            # which NO line parses keeps the False answer, which the
            # shipped malformed-journal behaviour depends on.
            return None
        return False
    except OSError:
        return None


def cleanup_old_sessions(
    project_slug: str,
    current_session_id: str,
    sessions_dir: str | None = None,
    max_age_days: int = _SESSION_MAX_AGE_DAYS,
    paused_max_age_days: int = _PAUSED_SESSION_MAX_AGE_DAYS,
    old_slug: str | None = None,
) -> None:
    """
    Remove stale session directories, applying a dual TTL.

    Sweeps the slug directory for ``project_slug`` and, when ``old_slug``
    differs from it, that directory too with the same TTL and carrier guard:
    a project launched through a symlink wrote earlier sessions under the
    unresolved name, and those age out beside the resolved one. Both slugs
    pass through the writers' sanitiser first, so a raw name reaches the
    directory the writers created and cannot name a path outside the root.

    Each candidate session directory is checked against a TTL selected per
    entry: checkpointed sessions (those whose journal contains any
    `session_paused` or `session_refreshed` event) use the extended
    `paused_max_age_days` threshold (default 180 days), while active
    sessions use `max_age_days` (default 30 days). The extended threshold
    protects in-progress user work across the pause→resume and
    refresh→resume workflows without retaining checkpoint state forever —
    checkpointed sessions still age out past 180 days.

    A directory's age runs from the newer of its own mtime and its newest
    direct child's (`_dir_max_child_mtime`); a directory whose children
    cannot be observed is kept.

    A directory older than its TTL is removed ONLY when
    `_journal_carries_unharvested_handoffs` returns False. It answers True
    for a journal that holds an `agent_handoff` event AFTER its last
    `session_consolidated` event. The test is POSITIONAL, so a session that
    consolidated and then kept working counts as a carrier. That knowledge
    reached no durable second carrier and nothing regenerates it. It
    answers None when the carrier question cannot be answered at all, which
    covers a read that raised AND a torn line together with at least one
    well-formed event. True and None each KEEP the directory, so this
    reaper holds the bytes on every path where the answer is protect or
    unknown.

    Best-effort cleanup — never raises. Skips the current session's
    directory and any entry that doesn't look like a UUID directory.

    Args:
        project_slug: Project identifier (basename of project_dir)
        current_session_id: Current session's UUID (never deleted)
        sessions_dir: Override for base directory (testing)
        max_age_days: TTL for active sessions in days (default: 30)
        paused_max_age_days: TTL for checkpointed (paused or refreshed)
            sessions in days (default: 180).
            Exposed as a kwarg so tests can inject smaller values for
            boundary verification; production call sites use the default.
        old_slug: The unresolved project basename; swept as well when it
            differs from ``project_slug``.
    """
    if not project_slug or not current_session_id:
        return

    if sessions_dir is None:
        sessions_dir = str(get_claude_config_dir() / "pact-sessions")

    slugs = [_UNSAFE_SLUG_CHARS_RE.sub("_", project_slug)]
    if old_slug:
        safe_old = _UNSAFE_SLUG_CHARS_RE.sub("_", old_slug)
        if safe_old != slugs[0]:
            slugs.append(safe_old)
    for slug in slugs:
        _reap_slug_dir(
            Path(sessions_dir) / slug,
            current_session_id,
            max_age_days,
            paused_max_age_days,
        )


def _reap_slug_dir(
    slug_dir: Path,
    current_session_id: str,
    max_age_days: int,
    paused_max_age_days: int,
) -> None:
    """Sweep one slug directory on the cleanup_old_sessions contract. Never
    raises."""
    if not slug_dir.exists():
        return

    try:
        for entry in slug_dir.iterdir():
            # Skip symlinks (live or dangling) — is_symlink uses lstat
            # semantics, short-circuiting before is_dir (which follows
            # symlinks). Prevents a planted link from pinning alive or
            # leaking mtime information about its target.
            if entry.is_symlink():
                continue
            if not entry.is_dir():
                continue
            if not _UUID_PATTERN.match(entry.name):
                continue
            if entry.name == current_session_id:
                continue
            try:
                # Age by the newer of the dir's own mtime and its newest
                # child's: a journal append or an in-place rewrite does not
                # move the dir's own mtime (see _dir_max_child_mtime), so a
                # live session would otherwise age out. The max can only make
                # an entry younger than its own mtime says. None means the
                # children could not be observed: keep the dir.
                child_mtime = _dir_max_child_mtime(entry, glob="*")
                if child_mtime is None:
                    continue
                newest = max(entry.stat().st_mtime, child_mtime)
                age_days = (time.time() - newest) / 86400
                # Select TTL per entry: checkpointed sessions (paused OR
                # refreshed) get the extended threshold; active sessions
                # get the standard one.
                threshold = (
                    paused_max_age_days
                    if _is_checkpointed_session(str(entry))
                    else max_age_days
                )
                if age_days > threshold:
                    # Carrier guard, below the age test so the added read
                    # runs only for an entry that is about to be removed.
                    # `is False` is load-bearing: True (the journal holds
                    # un-harvested HANDOFFs) and None (the carrier question
                    # is un-evaluable) must EACH skip the removal. A truth
                    # test on the negation removes on None, which is the
                    # fail-open direction. `is False` also skips on a
                    # future return value outside the contract.
                    if _journal_carries_unharvested_handoffs(
                        str(entry)
                    ) is False:
                        shutil.rmtree(entry, ignore_errors=True)
            except Exception:
                # Wider than OSError, and the width is load-bearing HERE
                # rather than in the predicate. A pathological journal line
                # can make the carrier test raise something that is not an
                # OSError, for example a RecursionError from deeply nested
                # JSON. Catching it at the predicate would hide a
                # programming error in the predicate itself. Catching it
                # for each ENTRY keeps the never-raises contract of this
                # function, skips the one bad entry, and lets the loop and
                # the cleanup_summary event that follows it continue.
                continue
    except OSError:
        pass


def _dir_max_child_mtime(entry: Path, glob: str = "*.json") -> float | None:
    """
    Return the max mtime across children of `entry` matching `glob`.

    Generalized helper used by all three reapers:
    - tasks reaper passes `glob="*.json"` — platform `TaskUpdate` rewrites
      individual `{id}.json` files; only *.json entries carry the signal.
    - teams reaper passes `glob="*"` — the team dir holds config.json
      AND member subdirectories AND arbitrary future sidecars; any child
      touch indicates the team is live.
    - session reaper (`_reap_slug_dir`) passes `glob="*"` and ages the
      session dir by the newer of this result and the dir's own mtime — a
      journal append rewrites a child without moving the dir's mtime.

    Why max-child rather than parent-dir stat: POSIX in-place overwrite
    (e.g. `config.json` rewrite via write-then-rename-or-truncate) does
    NOT bump the parent directory's mtime — the parent's mtime only
    changes on create/unlink/rename of its entries. So a team dir whose
    config.json is rewritten in place but has no member subdirs created
    would false-reap on parent-dir mtime. Max-child mtime is the tight
    upper bound on "when was anything under this dir last touched."

    Return values (cycle-5 refinement):
    - `float`: either a successful max-child mtime, OR the parent's
      `lstat().st_mtime` when the dir is legitimately empty (no children
      matched the glob).
    - `None` sentinel: "could not determine age." Two triggers:
      (a) outer `entry.glob()` raised OSError AND parent `lstat()` also
      raised — we can't enumerate OR fall back; OR
      (b) at least one child was observed but none yielded a positive
      mtime — EVERY `child.lstat()` raised, or every observed mtime is at
      or below 0 — distinguishable from empty-dir because we saw children.
      Callers MUST skip the entry on `None` rather than proceed to an age
      calculation that would collapse "can't observe" into "use parent
      mtime" (a false-reap risk under permission regressions). The tasks
      and teams reapers count the entry as skipped; the session reaper
      keeps the dir and counts nothing. The empty-dir case keeps the old
      semantic (fall back to parent mtime so stale empty dirs still age
      out).

    Fail-open: never raises. Returns a valid mtime or `None` in every
    branch. The parent-stat fallback uses `lstat()` (symlink-own
    semantics) for defense-in-isolation against callers that might
    forget an `is_symlink` guard — cycle-2 F2 pattern.

    Args:
        entry: Directory to probe.
        glob: Glob pattern selecting which children to consult. Default
            `"*.json"` matches the tasks-reaper convention; the teams and
            session reapers pass `"*"` to walk all children.

    Returns:
        Max child mtime, or parent mtime on empty-dir, or `None` sentinel
        when age cannot be determined (see above).
    """
    latest = 0.0
    saw_any_child = False
    try:
        for child in entry.glob(glob):
            saw_any_child = True
            try:
                # lstat() uses symlink-own semantics (no dereference). A
                # symlink child (attacker-planted `tasks/{real-dir}/x.json`
                # → `/var/log/syslog`) must NOT be allowed to pin the
                # parent's effective mtime to an arbitrary target; the
                # link's own mtime is the correct signal. lstat is the
                # portable pre-3.10 form (stat(follow_symlinks=False)
                # requires Python 3.10+).
                latest = max(latest, child.lstat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    if latest > 0.0:
        return latest
    # latest == 0.0 here. Two distinct scenarios:
    # - saw_any_child=False: legitimately empty (or outer glob raised
    #   before yielding). Fall back to parent mtime so stale empties age
    #   out — the intended empty-dir semantic.
    # - saw_any_child=True: we saw children but every child.lstat()
    #   raised. Collapsing this into "use parent mtime" would lose the
    #   signal that we CAN'T observe the dir. Return sentinel so the
    #   caller skips instead of false-reaping under a permission skew.
    if saw_any_child:
        return None
    try:
        # lstat (not stat) — cycle-5 defensive-in-isolation: the caller
        # already filters symlinks via is_symlink before calling us, but
        # using lstat here makes the helper safe even when called in
        # isolation (e.g. from future consumers that forget the guard).
        return entry.lstat().st_mtime
    except OSError:
        # Can neither observe children nor the parent — sentinel.
        return None


def cleanup_old_teams(
    current_team_name: str,
    teams_base_dir: str | None = None,
    max_age_days: int = _SESSION_MAX_AGE_DAYS,
) -> tuple[int, int]:
    """
    Remove stale team directories under ~/.claude/teams/ (issue #412 Fix B).

    Three defense layers:
    1. Name-pattern gate — only directories matching `_TEAM_NAME_PATTERN`
       (`^pact-[a-f0-9-]+$`) are candidates. This gate is DELIBERATELY
       NARROWER than `generate_team_name`'s current `session-<id8>` output:
       after the `pact-<id8>` -> `session-<id8>` migration the platform
       owns teardown of its own `session-*` namespace, so this reaper must
       NEVER reap live `session-*` dirs (widening `^pact-` to match
       `session-*` would arm `shutil.rmtree` against platform-owned dirs).
       See the "Reaper coupling" note on `generate_team_name` in
       shared/pact_context.py. Non-PACT writers that create
       `~/.claude/teams/foo-bar/` are out of scope: `~/.claude/teams/` is
       shared space, not PACT-owned space.
    2. Current-team skip — exact-match skip of `current_team_name`.
    3. Fail-closed on empty `current_team_name` — returns (0, 0) without
       reaping anything. An empty skip key combined with a permissive
       name filter would be catastrophic; the guard is belt-and-suspenders
       against a callsite bug even though layer (1) already filters.

    Age probe walks child mtimes via `_dir_max_child_mtime(entry, glob="*")`.
    Parent-dir mtime is wrong here: POSIX in-place overwrites (e.g.
    `config.json` rewritten without rename/unlink) do NOT bump the
    parent's mtime — only create/unlink/rename of entries does. Walking
    ALL children ("*") covers both the config.json-rewrite case AND the
    SubagentStart member-subdir creation case, giving a tight upper
    bound on "when was this team dir last touched."

    Best-effort: never raises. Swallows OSError per-entry and outer.

    Args:
        current_team_name: Current session's team_name from
            pact_context.get_team_name(). MUST be non-empty.
        teams_base_dir: Override for base directory (testing). Defaults
            to ~/.claude/teams.
        max_age_days: TTL in days (default: 30).

    Returns:
        (reaped, skipped) — `reaped` counts directories the TTL predicate
        selected and passed to `shutil.rmtree(..., ignore_errors=True)`;
        because `ignore_errors=True` swallows permission/EBUSY failures,
        `reaped` is attempted-deletions, NOT verified-deletions. `skipped`
        counts entries where stat/rmtree raised OSError before the rmtree
        dispatch (i.e. the TTL probe itself failed).
    """
    if not current_team_name:
        return 0, 0

    if teams_base_dir is None:
        teams_base_dir = str(get_claude_config_dir() / "teams")
    base = Path(teams_base_dir)
    if not base.exists():
        return 0, 0

    reaped = 0
    skipped = 0
    try:
        for entry in base.iterdir():
            # Skip symlinks (live or dangling) — is_symlink uses lstat
            # semantics, short-circuiting before is_dir (which follows
            # symlinks). Prevents a planted link from pinning alive or
            # leaking mtime information about its target.
            if entry.is_symlink():
                continue
            if not entry.is_dir():
                continue
            # Name-shape gate: only touch `pact-`-prefixed team dirs
            # (legacy/orphaned). DELIBERATELY narrower than
            # generate_team_name's current `session-<id8>` output: the
            # platform owns teardown of its own `session-*` namespace, so
            # this reaper must NEVER reap them (do NOT widen `^pact-` to
            # match `session-*`). Non-matching entries belong to other
            # tooling and are out of scope for this reaper.
            if not _TEAM_NAME_PATTERN.match(entry.name):
                continue
            # Case-insensitive skip (cycle-5 defensive): pact_context's
            # `get_team_name()` lowercases its return value and the
            # generate_team_name INVARIANT pins lowercase, so byte-exact
            # compare is correct-by-coincidence today. `.lower()` on both
            # sides tolerates future drift in either producer without a
            # silent reap of the current session's dir.
            if entry.name.lower() == current_team_name.lower():
                continue
            try:
                mtime = _dir_max_child_mtime(entry, glob="*")
                # Cycle-5 sentinel check: `None` means the helper couldn't
                # determine the dir's effective age (all child stats
                # raised, or glob + parent lstat both raised). Treat as
                # "cannot observe" → skipped; do NOT proceed to the age
                # calculation (which would TypeError on None anyway, but
                # an explicit guard makes the invariant self-documenting).
                if mtime is None:
                    skipped += 1
                    continue
                age_days = (time.time() - mtime) / 86400
                if age_days > max_age_days:
                    shutil.rmtree(entry, ignore_errors=True)
                    reaped += 1
            except OSError:
                skipped += 1
                continue
    except OSError:
        pass
    return reaped, skipped


def cleanup_old_tasks(
    skip_names: set[str],
    tasks_base_dir: str | None = None,
    max_age_days: int = _SESSION_MAX_AGE_DAYS,
) -> tuple[int, int]:
    """
    Remove stale task subdirectories under ~/.claude/tasks/ (issue #412 Fix B).

    Skips every entry whose name is in `skip_names`. Fails closed —
    returns (0, 0) if `skip_names` is empty or contains only blank
    strings. Per-entry mtime is probed via
    `_dir_max_child_mtime(entry, glob="*.json")` because platform writes
    update individual `{id}.json` files without bumping the parent dir's
    mtime.

    Best-effort: never raises. Swallows OSError per-entry and outer.

    Args:
        skip_names: Set of current-session names to preserve. Must
            contain at least one non-blank entry. Caller assembles
            {team_name, task_list_id, session_id} filtering empties.
        tasks_base_dir: Override for base directory (testing). Defaults
            to ~/.claude/tasks.
        max_age_days: TTL in days (default: 30).

    Returns:
        (reaped, skipped) — same semantics as cleanup_old_teams: `reaped`
        is attempted-deletions (rmtree called with ignore_errors=True, so
        failures are silent), `skipped` is entries where the TTL probe or
        rmtree dispatch itself raised OSError.
    """
    if not skip_names or all(not n for n in skip_names):
        return 0, 0

    if tasks_base_dir is None:
        tasks_base_dir = str(get_claude_config_dir() / "tasks")
    base = Path(tasks_base_dir)
    if not base.exists():
        return 0, 0

    reaped = 0
    skipped = 0
    try:
        for entry in base.iterdir():
            # Skip symlinks (live or dangling) — is_symlink uses lstat
            # semantics, short-circuiting before is_dir (which follows
            # symlinks). Prevents a planted link from pinning alive or
            # leaking mtime information about its target.
            if entry.is_symlink():
                continue
            if not entry.is_dir():
                continue
            if entry.name in skip_names:
                continue
            try:
                mtime = _dir_max_child_mtime(entry, glob="*.json")
                # Cycle-5 sentinel check: `None` means the helper couldn't
                # determine the dir's effective age. Skip rather than
                # false-reap under a permission regression.
                if mtime is None:
                    skipped += 1
                    continue
                age_days = (time.time() - mtime) / 86400
                if age_days > max_age_days:
                    shutil.rmtree(entry, ignore_errors=True)
                    reaped += 1
            except OSError:
                skipped += 1
                continue
    except OSError:
        pass
    return reaped, skipped


def _assemble_tasks_skip_set(
    team_name: str,
    task_list_id: str,
    session_id: str,
) -> set[str]:
    """
    Build the skip-set for `cleanup_old_tasks` from the three platform-
    key channels that can address `~/.claude/tasks/{name}/`.

    The three channels:
    - `team_name` — PACT canonical (from pact_context.get_team_name()).
      Bounded by the `generate_team_name` producer-side filter, but a
      non-PACT writer or future producer drift could still leak unsafe
      values, so the same allowlist applies (cycle-7 symmetry).
    - `task_list_id` — user-controlled env var `CLAUDE_CODE_TASK_LIST_ID`
      (platform-sourced). The positive-regex allowlist prevents a
      crafted value from bypassing the skip-set via unicode line
      terminators or path separators. Per PR #426 cycle-1 finding
      (patterns_path_name_fallback_escape) — the allowlist matches
      real-world task_list_id shapes (hex, uuid, alphanumeric ids)
      while rejecting dots, slashes, null bytes, and control chars
      by construction.
    - `session_id` — bare Claude Code fallback per
      `task_utils.get_task_list` (platform-sourced via SessionStart
      stdin). Flows through the SAME allowlist as `task_list_id`
      (cycle-5 symmetry) — defense-in-depth should not asymmetrically
      trust one channel.

    Fail-discard on allowlist mismatch: a failing value is silently
    dropped. The skip-set is ADDITIVE — missing a skip entry means we
    fall back to the other keys that DID pass, so discarding is
    strictly safer than trusting an untrusted value as a path key.
    Empty-string members are pruned by `discard("")`, so the caller
    does not need to pre-filter empties.

    Extracted from `main()` for direct unit testability — the function
    takes only primitives and returns a deterministic set, so callers
    can assert skip-set contents without mocking the session context.

    Args:
        team_name: Raw team_name from pact_context. May be empty.
        task_list_id: Raw CLAUDE_CODE_TASK_LIST_ID env var. May be empty.
        session_id: Raw session_id from pact_context. May be empty.

    Returns:
        The skip-set, with empty strings and allowlist-failing values
        removed. Caller treats a non-empty return as "the tasks reaper
        is safe to run"; empty means "all channels short-circuited or
        failed — do NOT run the tasks reaper" (fail-closed).
    """
    safe_team_name = team_name if is_safe_path_component(team_name) else ""
    safe_task_list_id = (
        task_list_id if is_safe_path_component(task_list_id) else ""
    )
    safe_session_id = session_id if is_safe_path_component(session_id) else ""
    skip_names = {safe_team_name, safe_task_list_id, safe_session_id}
    skip_names.discard("")
    return skip_names


def _is_safe_team_segment(team: str) -> bool:
    """Return True iff ``team`` is a single safe path component — usable to build
    a ``teams/<team>`` path without raising or escaping the teams root.

    The ``@team`` half of a registry value is SELF-ASSERTED and unsanitized (only
    the name half is sanitized at write, since team is config-validated on read),
    so a garbled/adversarial value could carry a NUL byte (an ``os.stat``/``open``
    syscall rejects it with ``ValueError: embedded null byte``; ``Path.is_dir()``
    only swallows it since Python 3.12), a path separator, or a ``..`` traversal
    that resolves to a real directory and is wrongly KEPT on every Python version.
    Legitimate team names are single lowercase-hex components (``pact-<hex>``, per
    ``generate_team_name``), so reject: empty, any C0 control char / DEL / NUL,
    ``/`` or ``\\``, and the traversal segments ``.`` / ``..``. Never raises.
    """
    if not team:
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in team):
        return False
    if "/" in team or "\\" in team:
        return False
    if team in (".", ".."):
        return False
    return True


def _prune_registry_dead_teams(
    registry_path: Path | None = None,
    teams_dir: Path | None = None,
) -> int:
    """Prune self-registration registry lines whose ``@team`` is no longer a
    live team directory under ``~/.claude/teams/``.

    The registry (``~/.claude/pact-sessions/.teammate-registry.jsonl``) grows one
    line per teammate per session. Last-wins-on-read makes stale lines harmless
    to correctness, but they accumulate, so SessionEnd drops the lines whose team
    has already been reaped — keeping the file bounded. A line is KEPT when its
    value's ``@team`` still has a directory under teams_dir; everything else
    (lines for reaped teams, malformed lines, lines with no ``@``, lines whose
    ``@team`` is not a safe single path segment) is dropped.

    Best-effort: never raises. The self-asserted ``@team`` is validated as a safe
    single path segment (``_is_safe_team_segment``) BEFORE any ``teams/<team>``
    path build, so a garbled/adversarial value cannot raise (e.g. a NUL byte) or
    escape the teams root. A missing registry / unreadable file / non-UTF-8
    content / write race is swallowed (the hook-fail-open invariant; a stale
    line is harmless). The
    rewrite preserves 0o600 and goes via a temp file renamed into place, so the
    registry is never open for writing: a failed write leaves the original
    intact, and a planted symlink cannot redirect it.

    Args:
        registry_path: the registry file. Defaults to the shared get_registry_path().
        teams_dir: the live-teams root. Defaults to ~/.claude/teams.

    Returns:
        Number of lines pruned (0 if the file is absent, the teams root cannot
        be observed, or nothing was stale).
    """
    if registry_path is None:
        registry_path = _get_registry_path()
    if teams_dir is None:
        teams_dir = get_claude_config_dir() / "teams"
    # SAFETY GUARANTEE 1 of 6 — the unobservable root. Absence or
    # unreadability of the teams root is NOT evidence that its teams are dead,
    # so a root this function cannot stat returns 0 and prunes nothing.
    #
    # os.stat rather than a directory PREDICATE: `Path.is_dir()` swallows
    # OSError INTERNALLY and returns False, so on a root the process cannot
    # traverse it would report every live team as dead from a call that never
    # raised — no exception, nothing for an `except` below to catch.
    #
    # NOT the idiom in `cleanup_old_teams` / `cleanup_old_tasks`, despite the
    # resemblance: those two survive an unenumerable root only because they
    # call `iterdir()`, which RAISES into their outer `except OSError`. This
    # function stats children one at a time, so no backstop fires and the
    # refusal has to be explicit.
    try:
        st = os.stat(teams_dir)
    except OSError:
        return 0  # cannot observe the teams root → prune nothing
    # SAFETY GUARANTEE 2 of 6 — a root that exists but is not a directory.
    # stat reports that a thing EXISTS, not that it is a directory: on a
    # plain-file root every per-entry stat below raises ENOTDIR, which is a
    # statement about a PATH COMPONENT rather than about the leaf, and reading
    # it as "that team is gone" empties the registry.
    #
    # What this line holds that guarantee 6 does not: ORDERING. Guarantee 6
    # catches the same ENOTDIR per entry, so no assertion on `pruned` or on
    # the file's contents can tell the two apart — delete this line and a
    # plain-file root still prunes nothing. What changes is that the registry
    # gets READ first. Refusing an unusable root before touching the file is
    # the property, and only a spy on the read can see it.
    if not stat.S_ISDIR(st.st_mode):
        return 0  # not a directory → the per-entry probes below are meaningless

    try:
        # SAFETY GUARANTEE 4 of 6 — a DELIBERATE symlink at the registry path
        # is refused here, before any write. Do not delete this as redundant
        # with the rewrite below: `os.replace` REPLACES a symlink rather than
        # refusing it, so without this line a user's deliberate link would be
        # silently swapped for a regular file.
        if not registry_path.exists() or registry_path.is_symlink():
            return 0
        raw = registry_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # SAFETY GUARANTEE 5 of 6 — a registry that is not valid UTF-8.
        # UnicodeDecodeError is named explicitly because it subclasses
        # ValueError, NOT OSError — without it, a registry holding invalid
        # UTF-8 raises straight out of a function whose contract is
        # never-raises. A half-written registry is a live possibility: the
        # rewrite below used to truncate in place, which could leave a
        # severed multi-byte sequence. Do not widen this to ValueError; a
        # corrupt registry is UNOBSERVABLE, not stale, so it prunes nothing.
        return 0

    kept_lines: list[str] = []
    pruned = 0
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        keep = False
        try:
            obj = json.loads(stripped)
            value = obj.get("value") if isinstance(obj, dict) else None
            if isinstance(value, str) and "@" in value:
                team = value.partition("@")[2]
                # Validate the self-asserted @team is a single safe path segment
                # BEFORE building an FS path: a garbled/adversarial @team (NUL,
                # control char, slash, '..') must never raise out of the prune
                # (honor the never-raises contract) nor build an uncontained
                # teams/<team> path.
                if _is_safe_team_segment(team):
                    try:
                        os.stat(teams_dir / team)
                        keep = True
                    except FileNotFoundError:
                        keep = False  # verified dead under this root
                    except OSError:
                        # SAFETY GUARANTEE 6 of 6 — an unobservable TEAM.
                        # One deletable unit, one property, one failure
                        # direction: delete it or turn it into `keep = False`
                        # and a team we cannot see becomes a team we call dead,
                        # dropping a live registration.
                        #
                        # PermissionError is an OSError and so is
                        # NotADirectoryError. Neither may reach the handler
                        # below, which would turn "I cannot tell" back into
                        # "drop it". Bail out instead, pruning nothing.
                        return 0
        except ValueError:
            # Malformed JSON only. Do NOT widen this back to OSError: that is
            # the route by which an unobservable team becomes a dropped line.
            keep = False
        if keep:
            kept_lines.append(stripped)
        else:
            pruned += 1

    if pruned == 0:
        return 0  # nothing stale → leave the file untouched (no needless rewrite)

    # Write a sibling temp and rename it into place: the registry is NEVER
    # open for writing, so there is no window in which it exists empty on
    # disk. Writing in place with O_TRUNC truncated at OPEN, so a failing
    # write (ENOSPC, EDQUOT, EIO — no adversary needed) left 0 bytes behind
    # and still returned 0, reporting "nothing pruned" over a destroyed file.
    #
    # SAFETY GUARANTEE 3 of 6 — the explicit 0600. os.replace does NOT
    # preserve the destination's mode, so whatever the TEMP carries becomes the
    # registry's. O_CREAT's mode argument sets 0600 here, but it is masked by
    # the process umask: at umask 022 the two agree and the fchmod changes
    # nothing, at umask 200 the mode argument alone yields 0400. The fchmod is
    # what makes the guarantee umask-INDEPENDENT — it is not what provides
    # 0600 in the normal case.
    #
    # Symlinks: O_CREAT|O_EXCL refuses to open one, and rename operates on the
    # link rather than its target, so a link planted here cannot redirect the
    # write. A deliberate link is refused earlier, at guarantee 4.
    tmp_path = registry_path.with_name(f"{registry_path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.fchmod(fd, 0o600)
            payload = ("\n".join(kept_lines) + "\n") if kept_lines else ""
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp_path, registry_path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass  # cleanup is best-effort; the registry is intact either way
        return 0  # never raise; the original file is untouched

    return pruned


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            input_data = {}

        pact_context.init(input_data)
        project_slug = get_project_slug()
        current_session_id = get_session_id()

        # Safety-net: warn if open PR detected but pause-mode wasn't run.
        # Returns a warning string (or None) so we can emit a single
        # session_end event with an optional `warning=` field.
        tasks = get_task_list()
        warning = check_unpaused_pr(
            tasks=tasks,
            project_slug=project_slug,
        )

        # Write a single session_end event to the journal (best-effort).
        # Wrapped in its own try/except so a journal failure does not skip
        # the cleanup steps that follow.
        try:
            event_kwargs = {"warning": warning} if warning else {}
            append_event(make_event("session_end", **event_kwargs))
        except Exception as e:
            print(f"Hook warning (session_end journal): {e}", file=sys.stderr)

        # Clean up stale session directories (dual TTL: 30d active, 180d
        # paused or refreshed)
        cleanup_old_sessions(
            project_slug=project_slug,
            current_session_id=current_session_id,
            old_slug=Path(get_project_dir()).name,
        )

        # Clean up stale ~/.claude/teams/ and ~/.claude/tasks/ (#412 Fix B).
        # Callsite short-circuit on empty team_name is the belt-and-suspenders
        # layer around the internal fail-closed guard.
        current_team_name = get_team_name()

        # Registry cleanup: SessionEnd also prunes the self-registration
        # registry (~/.claude/pact-sessions/.teammate-registry.jsonl), dropping
        # lines whose @team no longer has a live directory under ~/.claude/teams/.
        # The registry grows one line per teammate per session; last-wins-on-read
        # keeps stale lines harmless to correctness, but they accumulate, so the
        # prune (after the teams reaper, so reaped teams are already gone) keeps
        # the file bounded. Best-effort + fail-safe: a missing file / write race
        # is swallowed and never blocks session termination. (Run AFTER
        # cleanup_old_teams so a team reaped this run is also pruned here.)

        teams_r, teams_s = 0, 0
        tasks_r, tasks_s = 0, 0
        teams_reaper_ran = False
        tasks_reaper_ran = False
        if current_team_name:
            teams_r, teams_s = cleanup_old_teams(
                current_team_name=current_team_name,
            )
            teams_reaper_ran = True

        _prune_registry_dead_teams()

        # Assemble skip-set via the module-level helper — see
        # `_assemble_tasks_skip_set` for the full rationale on the three
        # platform-key channels and the positive-regex allowlist. The
        # helper takes only primitives so it's directly unit-testable
        # without mocking the session context.
        skip_names = _assemble_tasks_skip_set(
            team_name=current_team_name,
            task_list_id=os.environ.get("CLAUDE_CODE_TASK_LIST_ID", ""),
            session_id=current_session_id or "",
        )
        if skip_names:
            tasks_r, tasks_s = cleanup_old_tasks(
                skip_names=skip_names,
            )
            tasks_reaper_ran = True

        # Best-effort audit record for the reapers. A journal write
        # failure does not undo the cleanup that already happened.
        # `teams_ran`/`tasks_ran` discriminate "reaper executed and
        # found nothing" (True, 0/0) from "reaper short-circuited at
        # callsite" (False, 0/0) per side — otherwise the two states
        # are indistinguishable in the journal. Cycle-8 replaces the
        # older single `reaper_ran` bool with per-reaper bools so an
        # auditor can tell WHICH side short-circuited. Likewise
        # `teams_ttl_days`/`tasks_ttl_days` replace the single
        # `ttl_days` — currently both default to `_SESSION_MAX_AGE_DAYS`
        # but the split future-proofs against TTL divergence (e.g. if
        # the tasks reaper ever gets a dual-TTL like cleanup_old_sessions).
        try:
            append_event(make_event(
                "cleanup_summary",
                teams_reaped=teams_r,
                teams_skipped=teams_s,
                tasks_reaped=tasks_r,
                tasks_skipped=tasks_s,
                teams_ttl_days=_SESSION_MAX_AGE_DAYS,
                tasks_ttl_days=_SESSION_MAX_AGE_DAYS,
                teams_ran=teams_reaper_ran,
                tasks_ran=tasks_reaper_ran,
            ))
        except Exception as e:
            print(f"Hook warning (cleanup_summary journal): {e}", file=sys.stderr)

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        print(f"Hook warning (session_end): {e}", file=sys.stderr)
        print(hook_error_json("session_end", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
