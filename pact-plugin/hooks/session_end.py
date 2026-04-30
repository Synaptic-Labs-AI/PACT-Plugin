#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/session_end.py
Summary: SessionEnd hook that writes a session_end journal event and performs
         session directory cleanup.
Used by: hooks.json SessionEnd hook

Actions:
1. Write session_end event to the session journal
2. Detect open PRs that were not paused (append warning to journal)
3. Clean up teachback warning markers (session-scoped + legacy slug-level)
4. Clean up stale session directories using a dual TTL (30 days active, 180 days paused)

Purely observational — no destructive operations on project files. Session
directory cleanup is best-effort and never blocks session termination.

Input: JSON from stdin with session context
Output: None (SessionEnd hooks cannot inject context)
"""

import json
import os
import re
import shutil
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
from shared.pact_context import get_project_dir, get_session_dir, get_session_id, get_team_name
from shared.session_journal import (
    append_event,
    make_event,
    read_events,
    read_last_event_from,
)

from shared.session_state import is_safe_path_component
from shared.task_utils import get_task_list

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def get_project_slug() -> str:
    """Derive project slug from session context (basename of project_dir)."""
    project_dir = get_project_dir()
    if project_dir:
        return Path(project_dir).name
    return ""


def cleanup_wake_registry(team_name: str) -> None:
    """Best-effort removal of inbox-wake STATE_FILE sidecars for the given team.

    Belt-and-suspenders for force-termination edge cases (SIGKILL, crash)
    where the primary skill-invocation Teardown path didn't run. Cannot
    stop the orphaned Monitors — those are agent-runtime tools unreachable
    from this hook context. Sidecar removal lets the next session's Arm
    cold-start cleanly instead of seeing a STATE_FILE pointing at a
    long-dead Monitor.

    Per-agent STATE_FILE: every agent (lead AND every teammate spawned in
    the team) owns its own `inbox-wake-state-{agent-name}.json`. This helper
    globs the entire family and unlinks each — the lead's
    `inbox-wake-state-team-lead.json` and every teammate's
    `inbox-wake-state-{teammate-name}.json`.

    D1 has no heartbeat sidecar — single STATE_FILE per agent only.

    Path-traversal discipline (#492/#543 risk class):
      - team_name validated via is_safe_path_component (existing helper).
      - resolved path asserted under teams_root via relative_to(teams_root).
      - Glob pattern `inbox-wake-state-*.json` is constrained to the validated
        team_dir; Path.glob returns paths anchored to team_dir, so symlink-
        escape via the glob result is closed by the prior relative_to check.
      - Path.unlink wrapped in try/except OSError (missing_ok=True suppresses
        FileNotFoundError; other OSError subtypes still raise — caught here
        per module-wide fail-open posture).
    """
    if not team_name or not is_safe_path_component(team_name):
        return  # fail-closed on invalid team name
    teams_root = (Path.home() / ".claude" / "teams").resolve()
    team_dir = (teams_root / team_name).resolve()
    try:
        team_dir.relative_to(teams_root)
    except ValueError:
        return  # team_dir escaped teams_root (symlink attack defense)
    try:
        for state_file in team_dir.glob("inbox-wake-state-*.json"):
            try:
                state_file.unlink(missing_ok=True)
            except OSError:
                pass  # fail-open per module convention
    except OSError:
        pass  # fail-open if glob itself fails (e.g., team_dir vanished)


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


def cleanup_teachback_markers(
    project_slug: str,
    session_dir: str | None = None,
    sessions_dir: str | None = None,
) -> None:
    """
    Remove teachback warning marker files from the session directory.

    Marker files (teachback-warned-{agent}-{task_id}) accumulate during a session
    and are no longer needed once the session ends. Cleanup is best-effort.

    Cleans two locations:
    1. Session-scoped dir: {slug}/{session_id}/teachback-warned-* (current format)
    2. Slug-level dir: {slug}/teachback-warned-* (legacy migration sweep)

    Args:
        project_slug: Project identifier for the session directory
        session_dir: The session-scoped directory path (from get_session_dir()).
            When provided, markers are cleaned from this directory.
        sessions_dir: Override for sessions base directory (for testing)
    """
    if not project_slug:
        return

    if sessions_dir is None:
        sessions_dir = str(Path.home() / ".claude" / "pact-sessions")

    # Clean session-scoped markers (current format)
    if session_dir:
        _sweep_teachback_markers(Path(session_dir))

    # Migration sweep: clean orphaned slug-level markers (pre-#345 format)
    slug_dir = Path(sessions_dir) / project_slug
    _sweep_teachback_markers(slug_dir)


def _sweep_teachback_markers(directory: Path) -> None:
    """Remove all teachback-warned-* files in a directory. Best-effort."""
    if not directory.exists():
        return
    try:
        for marker in directory.iterdir():
            if marker.name.startswith("teachback-warned-"):
                try:
                    marker.unlink()
                except OSError:
                    pass
    except OSError:
        pass


# Regex for validating UUID-format directory names (session IDs).
# `\Z` (strict end-of-string) is used instead of `$`: in Python `re`,
# `$` matches end-of-string OR immediately before a trailing newline,
# so `deadbeef-dead-beef-dead-beefdeadbeef\n` would pass a `$` anchor
# and re-enter the skip-set / reaper allowlist as a crafted name.
# `\Z` rejects trailing newlines and is the stricter anchor.
_UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z'
)

# Regex for validating PACT team directory names. Intentionally LOOSER
# than what `generate_team_name` in session_init.py actually emits —
# the producer emits `pact-` + `secrets.token_hex(4)` (8 lowercase hex
# chars, no internal hyphens) or the session-id-prefix fallback
# (`pact-` + 8 hex chars). This regex accepts any `pact-`-prefixed
# lowercase-hex-and-hyphen shape so the reaper tolerates future drift
# in the producer (e.g. a naming scheme that introduces internal
# hyphens) without silently reaping a live team dir.
# Non-matching entries in ~/.claude/teams/ belong to other tooling and
# MUST NOT be reaped by cleanup_old_teams, even if they're stale by
# mtime. The reaper treats ~/.claude/teams/ as shared space, not
# PACT-owned space. `\Z` (strict end-of-string) — see _UUID_PATTERN.
_TEAM_NAME_PATTERN = re.compile(r'^pact-[a-f0-9-]+\Z')

# Default threshold for active (non-paused) session directory cleanup.
# 30 days balances disk usage (~50KB × 30 sessions = ~1.5MB) against
# cross-session recovery value.
_SESSION_MAX_AGE_DAYS = 30

# Extended threshold for paused session directories. Paused state is
# in-progress user work that has not been consolidated to memory or merged,
# so it gets a longer TTL than active sessions to protect the pause→resume
# workflow across long gaps. The extended TTL is protection, not permanent
# retention — paused sessions still age out past this threshold.
_PAUSED_SESSION_MAX_AGE_DAYS = 180

# Checkpoint file expiration for ~/.claude/pact-refresh/*.json. 7 days
# matches the prior refresh/constants.py CHECKPOINT_MAX_AGE_DAYS value.
# This cleanup is primarily a one-time sweep for existing deployments —
# with precompact_refresh.py removed (#413), no new checkpoints are
# written, so the directory asymptotically empties.
_CHECKPOINT_MAX_AGE_DAYS = 7


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


def cleanup_old_sessions(
    project_slug: str,
    current_session_id: str,
    sessions_dir: str | None = None,
    max_age_days: int = _SESSION_MAX_AGE_DAYS,
    paused_max_age_days: int = _PAUSED_SESSION_MAX_AGE_DAYS,
) -> None:
    """
    Remove stale session directories, applying a dual TTL.

    Each candidate session directory is checked against a TTL selected per
    entry: paused sessions (those whose journal contains any
    `session_paused` event) use the extended `paused_max_age_days`
    threshold (default 180 days), while active sessions use
    `max_age_days` (default 30 days). The extended threshold protects
    in-progress user work across the pause→resume workflow without
    retaining paused state forever — paused sessions still age out past
    180 days.

    Best-effort cleanup — never raises. Skips the current session's
    directory and any entry that doesn't look like a UUID directory.

    Args:
        project_slug: Project identifier (basename of project_dir)
        current_session_id: Current session's UUID (never deleted)
        sessions_dir: Override for base directory (testing)
        max_age_days: TTL for active sessions in days (default: 30)
        paused_max_age_days: TTL for paused sessions in days (default: 180).
            Exposed as a kwarg so tests can inject smaller values for
            boundary verification; production call sites use the default.
    """
    if not project_slug or not current_session_id:
        return

    if sessions_dir is None:
        sessions_dir = str(Path.home() / ".claude" / "pact-sessions")

    slug_dir = Path(sessions_dir) / project_slug
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
                age_days = (time.time() - entry.stat().st_mtime) / 86400
                # Select TTL per entry: paused sessions get the extended
                # threshold; active sessions get the standard one.
                threshold = (
                    paused_max_age_days
                    if _is_paused_session(str(entry))
                    else max_age_days
                )
                if age_days > threshold:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass


def _dir_max_child_mtime(entry: Path, glob: str = "*.json") -> float | None:
    """
    Return the max mtime across children of `entry` matching `glob`.

    Generalized helper used by both reapers:
    - tasks reaper passes `glob="*.json"` — platform `TaskUpdate` rewrites
      individual `{id}.json` files; only *.json entries carry the signal.
    - teams reaper passes `glob="*"` — the team dir holds config.json
      AND member subdirectories AND arbitrary future sidecars; any child
      touch indicates the team is live.

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
      (b) at least one child was observed but EVERY `child.lstat()`
      raised — distinguishable from empty-dir because we saw children.
      Callers MUST treat `None` as "skip this entry, count as skipped"
      rather than proceeding to an age calculation that would collapse
      "can't observe" into "use parent mtime" (a false-reap risk under
      permission regressions). The empty-dir case keeps the old semantic
      (fall back to parent mtime so stale empty dirs still age out).

    Fail-open: never raises. Returns a valid mtime or `None` in every
    branch. The parent-stat fallback uses `lstat()` (symlink-own
    semantics) for defense-in-isolation against callers that might
    forget an `is_symlink` guard — cycle-2 F2 pattern.

    Args:
        entry: Directory to probe.
        glob: Glob pattern selecting which children to consult. Default
            `"*.json"` matches the tasks-reaper convention; teams reaper
            passes `"*"` to walk all children (config.json + subdirs).

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
       (`^pact-[a-f0-9-]+$`) are candidates. This mirrors the INVARIANT
       documented on `generate_team_name` in session_init.py. Non-PACT
       writers that create `~/.claude/teams/foo-bar/` are out of scope:
       `~/.claude/teams/` is shared space, not PACT-owned space.
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
        teams_base_dir = str(Path.home() / ".claude" / "teams")
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
            # Name-shape gate: only touch PACT-shaped team dirs. Mirrors
            # the generate_team_name INVARIANT in session_init.py. Non-
            # matching entries belong to other tooling and are out of
            # scope for this reaper.
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
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")
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


def _cleanup_old_checkpoints(
    checkpoint_dir: Path | None = None,
    max_age_days: int = _CHECKPOINT_MAX_AGE_DAYS,
) -> int:
    """
    Remove checkpoint files older than max_age_days from ~/.claude/pact-refresh/.

    Post-#413, the precompact_refresh.py hook that wrote these files is deleted,
    so this cleanup is primarily a one-time sweep for existing deployments —
    a directory that never gets written to will eventually empty.

    Best-effort: never raises. Swallows per-file OSError (handles races) and
    the outer glob failure (hook-fail-open invariant).

    Args:
        checkpoint_dir: Directory containing checkpoint files. Defaults to
            ~/.claude/pact-refresh. Accepts override for testing.
        max_age_days: TTL for checkpoint files (default: 7).

    Returns:
        Number of files cleaned up.
    """
    if checkpoint_dir is None:
        checkpoint_dir = Path.home() / ".claude" / "pact-refresh"

    if not checkpoint_dir.exists():
        return 0

    max_age_seconds = max_age_days * 24 * 60 * 60
    cutoff_time = time.time() - max_age_seconds
    cleaned = 0

    try:
        for checkpoint_file in checkpoint_dir.glob("*.json"):
            # Skip symlinks (live or dangling). Mirrors cycle-1 hardening
            # on the three sibling reapers — `is_symlink()` uses lstat
            # semantics, so it short-circuits before any follow-semantic
            # probe. Prevents a planted link from driving the TTL oracle
            # off the target's mtime or from unlinking the link when the
            # link's own mtime is within TTL but the target's is older.
            if checkpoint_file.is_symlink():
                continue
            try:
                # lstat (not stat) — cycle-2 defense: even with the
                # symlink guard above, lstat is the correct probe for
                # the link's own mtime if the guard is ever removed or
                # if a future caller bypasses it. Defense-in-isolation.
                mtime = checkpoint_file.lstat().st_mtime
                if mtime < cutoff_time:
                    checkpoint_file.unlink()
                    cleaned += 1
            except OSError:
                pass
    except OSError:
        pass

    return cleaned


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            input_data = {}

        pact_context.init(input_data)
        project_slug = get_project_slug()
        session_dir = get_session_dir()
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

        # Clean up teachback warning markers (no longer needed after session)
        cleanup_teachback_markers(
            project_slug=project_slug,
            session_dir=session_dir,
        )

        # Clean up stale session directories (dual TTL: 30d active, 180d paused)
        cleanup_old_sessions(
            project_slug=project_slug,
            current_session_id=current_session_id,
        )

        # Clean up stale ~/.claude/teams/ and ~/.claude/tasks/ (#412 Fix B).
        # Callsite short-circuit on empty team_name is the belt-and-suspenders
        # layer around the internal fail-closed guard.
        current_team_name = get_team_name()

        # Wake-registry cleanup (#591). Belt-and-suspenders for force-
        # termination paths. Cannot reach TaskStop from hook context;
        # only the registry sidecar is removable here. D1 has no
        # heartbeat sidecar — single STATE_FILE per agent only. Glob
        # `inbox-wake-state-*.json` to catch lead AND every teammate's
        # sidecar in one pass (symmetric per-agent arming, §15.4).
        if current_team_name:
            cleanup_wake_registry(current_team_name)

        teams_r, teams_s = 0, 0
        tasks_r, tasks_s = 0, 0
        teams_reaper_ran = False
        tasks_reaper_ran = False
        if current_team_name:
            teams_r, teams_s = cleanup_old_teams(
                current_team_name=current_team_name,
            )
            teams_reaper_ran = True

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

        # Clean up stale pact-refresh checkpoint files (7-day TTL).
        # Post-#413, these accumulate only in legacy deployments.
        _cleanup_old_checkpoints()

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        print(f"Hook warning (session_end): {e}", file=sys.stderr)
        print(hook_error_json("session_end", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
