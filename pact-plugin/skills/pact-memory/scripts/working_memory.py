"""
Working Memory Sync Module

Location: pact-plugin/skills/pact-memory/scripts/working_memory.py

Summary: Handles synchronization of memories to the Working Memory section
in CLAUDE.md. Maintains a rolling window of the most recent memories for
quick reference during Claude sessions. Applies token budgets to prevent
unbounded growth of memory sections.

Used by:
- memory_api.py: Calls sync_to_claude_md() after saving memories
- Test files: test_working_memory.py tests all functions in this module
"""

import fcntl
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Configure logging
logger = logging.getLogger(__name__)

# Constants for working memory section (saved memories).
# Working Memory provides structured, PACT-specific context (goals, decisions,
# lessons) synced from the SQLite database. It coexists with the platform's
# auto-memory (MEMORY.md), which captures free-form session learnings. Reduced
# from 5 to 3 entries to limit token overlap between the two systems while
# retaining the structured format that auto-memory does not provide.
WORKING_MEMORY_HEADER = "## Working Memory"
WORKING_MEMORY_COMMENT = "<!-- Auto-managed by pact-memory skill. Last 3 memories shown. Full history searchable via pact-memory skill. -->"
MAX_WORKING_MEMORIES = 3

# Constants for retrieved context section (searched/retrieved memories)
RETRIEVED_CONTEXT_HEADER = "## Retrieved Context"
RETRIEVED_CONTEXT_COMMENT = "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->"
MAX_RETRIEVED_MEMORIES = 3

# Token budget constants.
# Approximation: 1 token ~ 0.75 words, so word_count * 1.3 ~ token count.
WORKING_MEMORY_TOKEN_BUDGET = 800
RETRIEVED_CONTEXT_TOKEN_BUDGET = 500
# Note: PINNED_CONTEXT_TOKEN_BUDGET is defined solely in hooks/staleness.py

# Pin caps constants (twin copy of hooks/pin_caps.py — cannot import across
# the skills-to-hooks package boundary). Drift-detection test in
# tests/test_staleness.py guards against divergence; if you change these,
# update hooks/pin_caps.py in the SAME commit.
#
# Forward-looking drift anchors: no skill-side code currently consumes these
# constants — they exist here solely so a future skills-side pin-cap
# consumer can read the budget without needing to cross the package
# boundary. Anchored only by TestPinCapsTwinCopyDrift. Do NOT remove
# even if unused at read time; the drift test + forward-compat intent are
# the justification for the twin copy.
PIN_COUNT_CAP = 12
PIN_SIZE_CAP = 1500
PIN_STALE_BLOCK_THRESHOLD = 2
OVERRIDE_RATIONALE_MAX = 120

# PACT-managed boundary marker prefixes. Used by _find_terminator_offset to
# terminate section scans on any PACT boundary marker. The canonical
# definition lives in hooks/shared/claude_md_manager.py as
# PACT_BOUNDARY_PREFIXES — this module cannot import from hooks/shared/
# (separate package boundary), so the alternation is inlined here. The
# three prefixes rarely change; if a 4th is added, update this string.
_PACT_BOUNDARY_ALT = "PACT_MEMORY_|PACT_MANAGED_|PACT_ROUTING_"

# Managed-region boundary markers. Twin copies of the canonical definitions
# in hooks/shared/claude_md_manager.py (cannot import — separate package).
_MANAGED_START_MARKER = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
_MANAGED_END_MARKER = "<!-- PACT_MANAGED_END -->"

# file_lock: vendored twin of hooks/shared/claude_md_manager.file_lock —
# skills/pact-memory/scripts/ cannot import from hooks/shared/ (separate
# package boundary). Cross-process correctness is preserved because
# fcntl.flock serializes on the sidecar inode, not the Python object: a hook
# process and this skill process locking the SAME .{name}.lock sidecar
# contend on the same kernel lock. The drift-detection test
# (TestFileLockTwinCopyDrift in tests/test_staleness.py) guards byte-alignment
# of the function body with the canonical copy; if you change either, update
# both in the SAME commit. The two constants below are part of the twin and
# must match the canonical values.
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_INTERVAL = 0.1


@contextmanager
def file_lock(target_file: Path):
    """Acquire an exclusive sidecar file lock for a target CLAUDE.md path.

    Twin of hooks/shared/claude_md_manager.file_lock — kept local because
    skills/pact-memory/scripts/ cannot import from hooks/shared/. Body MUST
    stay byte-identical to the canonical copy (drift test enforces this).

    NOT RE-ENTRANT: fcntl.flock is non-re-entrant at the OS level. Nesting one
    sync site inside another under the SAME target would self-deadlock until the
    fail-open TimeoutError (after _LOCK_TIMEOUT_SECONDS). This is not reachable
    on the current call graph — the two sync sites are independent top-level
    calls, never nested — so no behavioral re-entrancy guard is added (a guard
    would alter this body and trip the drift test; the OS-level non-re-entrancy
    plus the callers' fail-open already bound the worst case).
    """
    # Resolve the WHOLE target, not just its parent: fcntl.flock serialises on
    # the sidecar's inode, so two spellings of one file must produce one sidecar
    # name. Resolving only the parent still keys a symlinked FILE by its alias
    # (two names, one inode -> two sidecars -> no mutual exclusion).
    resolved_target = target_file.resolve()
    lock_path = resolved_target.parent / f".{resolved_target.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # 0o600: the lock file is adjacent to user-private CLAUDE.md content;
    # match the same permissions to avoid leaving a world-readable sidecar.
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    try:
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    # S8 (security-engineer-review): emit a stderr
                    # warning before raising. Callers fail-open on
                    # TimeoutError (skip the cleanup pass), so without
                    # this warning a stuck holder would silently defer
                    # kernel-block / managed-block cleanup forever.
                    # Stderr from hooks does not surface in the user
                    # transcript, but it does land in Claude Code's
                    # debug logs — repeated warnings make the
                    # contention-vs-bug class observable.
                    print(
                        f"PACT file_lock timeout: failed to acquire "
                        f"lock on {lock_path} within "
                        f"{_LOCK_TIMEOUT_SECONDS}s; falling open",
                        file=sys.stderr,
                    )
                    raise TimeoutError(
                        f"Failed to acquire lock on {lock_path} within "
                        f"{_LOCK_TIMEOUT_SECONDS}s"
                    )
                time.sleep(_LOCK_POLL_INTERVAL)
        yield
    finally:
        # Release before close. flock is released automatically on fd close
        # by the kernel, but an explicit LOCK_UN ensures immediate release
        # even if close is delayed (e.g., by subsequent finalizer work).
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


# Why the sync sites lock the WHOLE read->mutate->write window (not just the
# write). This rationale is shared by both sync_to_claude_md and
# sync_retrieved_to_claude_md, which each carry only a short pointer back here.
# (Distinct from the "why an inline twin" note above: that explains WHY the lock
# is vendored; this explains WHY the lock spans the whole window.)
#   - read-under-lock is the load-bearing no-clobber property: a write-only lock
#     would let a 2nd writer read stale (pre-this-write) content, mutate it, and
#     overwrite this writer's entry the instant the lock releases — the exact
#     lost update the lock exists to prevent.
#   - lock identity is the sidecar inode of the RESOLVED CLAUDE.md path, so this
#     serializes against session_init / session_resume: all writers resolve to
#     the same .claude/CLAUDE.md (CLAUDE_PROJECT_DIR is set every session → all
#     hit the env-var branch first) and thus share one .CLAUDE.md.lock sidecar.
#   - CLAUDE_PROJECT_DIR edge: if it were ever unset AND the git-root/cwd
#     fallbacks diverged between processes, the sidecars would differ and the
#     lock would not serialize — accepted as out-of-contract (no safe fallback
#     action exists if the paths genuinely diverge).


def _atomic_write_text(target: Path, content: str) -> None:
    """Replace `target`'s contents with `content` atomically.

    `Path.write_text` truncates the file and THEN writes, so a crash, a full
    disk, or a kill between those two steps leaves a TRUNCATED CLAUDE.md. In the
    projects this runs in that file is gitignored and untracked, so there is no
    recovery path -- the user's pinned context is simply gone.

    Writing to a sibling temp file and renaming makes the replacement atomic: a
    reader sees either the whole old file or the whole new one, never a partial
    write. The temp file is created in the TARGET'S OWN DIRECTORY because
    `os.replace` is only atomic within a single filesystem.

    The mode is set on the TEMP file before the rename, so the target is never
    momentarily visible with the wrong permissions -- unlike a chmod after the
    write, which leaves exactly such a window on a file holding user content.

    Callers must already hold `file_lock` for the target. The lock closes the
    concurrent-writer window; this closes the crash/truncation window. They are
    different hazards, and neither fix subsumes the other. Note the lock is a
    separate sidecar file, so replacing the target's inode does not disturb it.

    Requires write permission on the target's DIRECTORY (to create the temp),
    where a bare `write_text` needed only permission on the file itself. A
    read-only directory holding a writable CLAUDE.md would now fail the write
    rather than truncate it -- a safe direction, but a real behaviour change.

    NOTE: a deliberate duplicate of `_atomic_write_text` in
    `hooks/shared/claude_md_manager.py`. This module cannot import from
    `hooks/shared/` (separate package), the same constraint that produced the
    `file_lock` twin above. Unlike that twin, the two copies are NOT
    drift-gated: atomicity has no cross-process invariant, so each copy is
    independently correct and may legitimately diverge.

    Args:
        target: Path to replace. Its parent directory must already exist.
        content: Full file contents to write.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        # os.fdopen takes ownership of fd only on success; if it raises, the
        # raw fd mkstemp opened would leak (the outer cleanup unlinks the temp
        # FILE but cannot close a descriptor it never received a handle for).
        try:
            handle = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        with handle:
            handle.write(content)
            handle.flush()
            # Without the fsync the rename can be persisted while the data
            # behind it is not, which reintroduces the empty-file failure this
            # function exists to prevent.
            os.fsync(handle.fileno())
        # mkstemp already creates 0o600; setting it explicitly keeps the mode a
        # property of this function rather than of the stdlib's default.
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    except BaseException:
        # Never leave a stray temp file behind next to the user's CLAUDE.md.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def extract_managed_region(content: str) -> Optional[Tuple[str, int]]:
    """
    Extract the PACT-managed region from CLAUDE.md content.

    Twin of hooks/shared/claude_md_manager.extract_managed_region — kept
    local because skills/pact-memory/scripts/ cannot import from hooks/shared/.

    Returns (region_text, start_offset) where start_offset is the absolute
    position of the first character after MANAGED_START_MARKER. Returns None
    if either marker is missing.
    """
    start_idx = content.find(_MANAGED_START_MARKER)
    if start_idx == -1:
        return None
    region_start = start_idx + len(_MANAGED_START_MARKER)
    end_idx = content.find(_MANAGED_END_MARKER, region_start)
    if end_idx == -1:
        return None
    return content[region_start:end_idx], region_start


def _find_existing_claude_md(base: Path) -> Optional[Path]:
    """
    Return the first existing CLAUDE.md under `base`, checking both
    supported locations in priority order.

    Claude Code accepts project memory at either `.claude/CLAUDE.md` (new
    default) or `./CLAUDE.md` (legacy). This helper checks `.claude/CLAUDE.md`
    first, then falls back to `./CLAUDE.md`, returning the first match or
    None if neither exists.

    Args:
        base: Directory to probe for CLAUDE.md.

    Returns:
        Path to the existing CLAUDE.md, or None if neither location exists.
    """
    dot_claude = base / ".claude" / "CLAUDE.md"
    if dot_claude.exists():
        return dot_claude
    legacy = base / "CLAUDE.md"
    if legacy.exists():
        return legacy
    return None


def _get_claude_md_path() -> Optional[Path]:
    """
    Get the path to CLAUDE.md in the project root.

    Uses CLAUDE_PROJECT_DIR environment variable if set, then falls back
    to git worktree/repo root detection, then to current working directory.
    At each level, checks both `.claude/CLAUDE.md` (new default) and
    `./CLAUDE.md` (legacy) in priority order.

    Note: This mirrors the resolution strategy in hooks/staleness.py
    (get_project_claude_md_path). Kept as a local copy because this
    module lives in skills/ and cannot import from hooks/.

    Returns:
        Path to CLAUDE.md if it exists, None otherwise.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        found = _find_existing_claude_md(Path(project_dir))
        if found is not None:
            return found

    # Fallback: detect git root (worktree-safe)
    # Uses --git-common-dir instead of --show-toplevel because the latter
    # returns the worktree path when run inside a worktree, which may not
    # contain CLAUDE.md. --git-common-dir always points to the shared .git
    # directory; its parent is the main repo root where CLAUDE.md lives.
    # git returns this path relative to the invoking directory when run at a
    # repo root (the bare ".git") and absolute elsewhere, so resolve a relative
    # result against the cwd before taking its parent.
    # NOTE: Twin pattern in memory_api.py (_detect_project_id) and
    #       hooks/staleness.py (get_project_claude_md_path) -- keep in sync.
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            common_dir = Path(result.stdout.strip())
            if not common_dir.is_absolute():
                common_dir = Path.cwd() / common_dir
            repo_root = common_dir.resolve().parent
            found = _find_existing_claude_md(repo_root)
            if found is not None:
                return found
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Last resort: current working directory
    return _find_existing_claude_md(Path.cwd())


def _resolve_display_claude_md_with_base() -> Tuple[Optional[Path], Optional[Path]]:
    """
    Resolve the display CLAUDE.md AND the trusted base directory it was found
    under, so a write caller can containment-check the target against the base
    the resolver actually used (#1247).

    Same resolution order as `_resolve_display_claude_md_path` (which is now a
    thin wrapper returning `[0]`):
      1. CLAUDE_PROJECT_DIR env var, if set -> that dir's .claude/CLAUDE.md
         (preferred) or ./CLAUDE.md (legacy).
      2. Git worktree root via `git rev-parse --show-toplevel` -> the same
         .claude/-then-legacy probe under the worktree root.
      3. Main repo root via `git rev-parse --git-common-dir`.parent -> the
         same probe. Reached only when the worktree is NOT a session root
         (branch 2 found nothing): under the PACT `.worktrees/` convention no
         session is rooted in the worktree, so the file the session reads is
         the main repo's. Without this branch that write is lost (returns
         None); with it the write lands where the session reads.
      4. Current working directory -> the same probe.

    Branch 2 anchors the WORKTREE root (--show-toplevel) so a worktree that IS
    a session root updates its OWN display file; branch 3 falls back to
    _get_claude_md_path's MAIN-repo anchor (--git-common-dir) for the common
    case where it is not. Because branch 2 precedes branch 3, the two resolvers
    now differ ONLY in that worktree-root branch: in a non-worktree checkout
    both branches resolve the same directory, so the [0] of this result is
    identical to _get_claude_md_path's.

    The returned `base` is the branch's directory captured BEFORE descending
    into `.claude` (the arg passed to `_find_existing_claude_md`), NOT the
    returned path and NOT a re-derivation. That is the trusted pre-resolve
    anchor that makes the #1247 containment check non-vacuous: an F1
    symlinked-parent `.claude` perturbs the target's resolve() but not the
    base's, so containment catches the escape.

    This never CREATES a CLAUDE.md (the orchestrator manages the file's
    lifecycle); it only probes for an existing one.

    Returns:
        (path, base) where path is the existing display CLAUDE.md and base is
        the directory it was found under; (None, None) if none exists.
    """
    # Resolution must never raise into the sync path; on any failure (a bad
    # CLAUDE_PROJECT_DIR value, an inaccessible probe target, or a deleted cwd)
    # return (None, None) so the caller skips the sync and the save still succeeds.
    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
        if project_dir:
            base = Path(project_dir)
            found = _find_existing_claude_md(base)
            if found is not None:
                return found, base

        # Worktree root: --show-toplevel returns the worktree directory when run
        # inside a worktree (and the main repo root otherwise), matching the
        # directory session_init/session_resume target for the session's CLAUDE.md.
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                worktree_root = Path(result.stdout.strip())
                found = _find_existing_claude_md(worktree_root)
                if found is not None:
                    return found, worktree_root
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # Main-repo root via --git-common-dir. Under the PACT `.worktrees/`
        # convention no session is ever rooted in the worktree, so branch 2
        # found nothing and the file the session actually reads is the MAIN
        # repo's. --git-common-dir points at the shared .git dir whether run
        # from the main repo or a linked worktree, so its parent is the main
        # repo root in both. This is _get_claude_md_path's exact anchor.
        #
        # The is_absolute() guard is load-bearing, not decoration: git returns
        # a RELATIVE path (".git", "../.git") when run at a repo root or subdir,
        # and _find_existing_claude_md does a bare `base / "CLAUDE.md"` with no
        # normalisation, so a relative base would yield a cwd-relative Path and
        # a cwd-relative lock sidecar (the exact divergence D2 just closed).
        # Reused verbatim from _get_claude_md_path.
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                common_dir = Path(result.stdout.strip())
                if not common_dir.is_absolute():
                    common_dir = Path.cwd() / common_dir
                repo_root = common_dir.resolve().parent
                found = _find_existing_claude_md(repo_root)
                if found is not None:
                    return found, repo_root
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # Last resort: current working directory
        cwd = Path.cwd()
        found = _find_existing_claude_md(cwd)
        return (found, cwd) if found is not None else (None, None)
    except Exception as e:
        logger.debug("display CLAUDE.md resolution failed, skipping sync: %s", e)
        return None, None


def _resolve_display_claude_md_path() -> Optional[Path]:
    """
    Resolve the CLAUDE.md the CURRENT SESSION displays (path only).

    Thin wrapper over `_resolve_display_claude_md_with_base` (added for #1247);
    read-only callers and the resolver-parity lint use this Path-only name,
    while the 2 write callers use the with-base variant to get the containment
    anchor. See that function for the full resolution order and the base
    semantics.

    Returns:
        Path to the existing display CLAUDE.md, or None if none exists.
    """
    return _resolve_display_claude_md_with_base()[0]


def _estimate_tokens(text: str) -> int:
    """
    Estimate token count for a text string.

    Uses word count multiplied by 1.3 as a simple approximation for
    English text. No external tokenizer dependency required.

    NOTE: Twin copy exists in hooks/staleness.py (estimate_tokens) -- keep in sync.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count (integer).
    """
    if not text:
        return 0
    return int(len(text.split()) * 1.3)


def _compress_memory_entry(entry: str) -> str:
    """
    Compress a full memory entry to a single-line summary.

    Preserves the date header and extracts the first sentence from the
    Context field. All other fields (Goal, Decisions, Lessons, Files,
    Memory ID) are dropped.

    Args:
        entry: Full markdown memory entry string starting with ### YYYY-MM-DD.

    Returns:
        Compressed entry with date header and one-line summary.
    """
    lines = entry.strip().split("\n")
    if not lines:
        return entry

    # Preserve the date header line (### YYYY-MM-DD HH:MM)
    date_line = lines[0]

    # Find the Context field and extract its first sentence
    summary_text = ""
    for line in lines[1:]:
        if line.startswith("**Context**:"):
            context_value = line.split("**Context**:", 1)[1].strip()
            # Take first sentence (up to first ". " boundary, or first 120 chars).
            # Uses ". " instead of "." to avoid truncating at version numbers
            # like v2.3.1 or decimal values.
            period_idx = context_value.find(". ")
            if period_idx > 0 and period_idx < 120:
                summary_text = context_value[:period_idx + 1]
            else:
                summary_text = context_value[:120]
                if len(context_value) > 120:
                    summary_text += "..."
            break

    if not summary_text:
        # Fallback: use first non-header line content
        for line in lines[1:]:
            stripped = line.strip()
            if stripped and stripped.startswith("**") and "**:" in stripped:
                # Extract value from any bold field
                summary_text = stripped.split("**:", 1)[1].strip()[:120]
                if len(stripped.split("**:", 1)[1].strip()) > 120:
                    summary_text += "..."
                break

    if summary_text:
        return f"{date_line}\n**Summary**: {summary_text}"
    return date_line


def _apply_token_budget(
    entries: List[str],
    token_budget: int
) -> List[str]:
    """
    Apply a token budget to a list of memory entries.

    Strategy: Keep the newest entry in full. Compress older entries to
    single-line summaries. If still over budget, reduce the number of
    entries shown.

    Args:
        entries: List of memory entry strings (newest first).
        token_budget: Maximum estimated tokens for all entries combined.

    Returns:
        List of entries (some possibly compressed) fitting within budget.
    """
    if not entries:
        return entries

    # Check if already within budget
    total_tokens = sum(_estimate_tokens(e) for e in entries)
    if total_tokens <= token_budget:
        return entries

    # Strategy: keep newest entry full, compress the rest
    result = [entries[0]]
    for entry in entries[1:]:
        compressed = _compress_memory_entry(entry)
        result.append(compressed)

    # Check if compressed version fits
    total_tokens = sum(_estimate_tokens(e) for e in result)
    if total_tokens <= token_budget:
        return result

    # Still over budget: drop entries from the end until we fit.
    # Subtract the popped entry's tokens instead of recalculating the full sum.
    while len(result) > 1 and total_tokens > token_budget:
        removed = result.pop()
        total_tokens -= _estimate_tokens(removed)

    return result


def _format_memory_entry(
    memory: Dict[str, Any],
    files: Optional[List[str]] = None,
    memory_id: Optional[str] = None
) -> str:
    """
    Format a memory as a markdown entry for CLAUDE.md.

    Args:
        memory: Memory dictionary with context, goal, decisions, etc.
        files: Optional list of file paths associated with this memory.
        memory_id: Optional memory ID to include for database reference.

    Returns:
        Formatted markdown string for the memory entry.
    """
    # Get date and time for header
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d %H:%M")

    lines = [f"### {date_str}"]

    # Add context if present
    if memory.get("context"):
        lines.append(f"**Context**: {memory['context']}")

    # Add goal if present
    if memory.get("goal"):
        lines.append(f"**Goal**: {memory['goal']}")

    # Add decisions if present
    decisions = memory.get("decisions")
    if decisions:
        if isinstance(decisions, list):
            # Extract decision text from list of dicts or strings
            decision_texts = []
            for d in decisions:
                if isinstance(d, dict):
                    decision_texts.append(d.get("decision", str(d)))
                else:
                    decision_texts.append(str(d))
            if decision_texts:
                lines.append(f"**Decisions**: {', '.join(decision_texts)}")
        elif isinstance(decisions, str):
            lines.append(f"**Decisions**: {decisions}")

    # Add lessons if present
    lessons = memory.get("lessons_learned")
    if lessons:
        if isinstance(lessons, list) and lessons:
            lines.append(f"**Lessons**: {', '.join(str(l) for l in lessons)}")
        elif isinstance(lessons, str):
            lines.append(f"**Lessons**: {lessons}")

    # Add reasoning chains if present
    reasoning = memory.get("reasoning_chains")
    if reasoning:
        if isinstance(reasoning, list) and reasoning:
            lines.append(f"**Reasoning chains**: {', '.join(str(r) for r in reasoning)}")
        elif isinstance(reasoning, str):
            lines.append(f"**Reasoning chains**: {reasoning}")

    # Add agreements if present
    agreements = memory.get("agreements_reached")
    if agreements:
        if isinstance(agreements, list) and agreements:
            lines.append(f"**Agreements**: {', '.join(str(a) for a in agreements)}")
        elif isinstance(agreements, str):
            lines.append(f"**Agreements**: {agreements}")

    # Add disagreements resolved if present
    disagreements = memory.get("disagreements_resolved")
    if disagreements:
        if isinstance(disagreements, list) and disagreements:
            lines.append(f"**Disagreements resolved**: {', '.join(str(d) for d in disagreements)}")
        elif isinstance(disagreements, str):
            lines.append(f"**Disagreements resolved**: {disagreements}")

    # Add files if present
    if files:
        lines.append(f"**Files**: {', '.join(files)}")

    # Add memory ID if provided
    if memory_id:
        lines.append(f"**Memory ID**: {memory_id}")

    return "\n".join(lines)


def _find_terminator_offset(
    content: str,
    start: int,
    terminator_pattern: "re.Pattern[str]",
) -> int:
    """
    Find the absolute offset of the first line matching `terminator_pattern`.

    Simple line-by-line search — no fence tracking needed because callers
    operate within the PACT-managed region (round 10 structural guarantee).
    The managed region contains only plugin-generated content; user-authored
    fenced code blocks live outside PACT_MANAGED_START/END.

    Args:
        content: Text to scan (typically the managed region extract, not
            the full file).
        start: Absolute offset in `content` where scanning begins.
        terminator_pattern: Compiled regex matched against individual lines
            via `.match`.

    Returns:
        Absolute offset of the first terminator line, or `len(content)` if
        none found.
    """
    pos = start
    while pos < len(content):
        nl = content.find("\n", pos)
        if nl == -1:
            line = content[pos:]
            line_end = len(content)
        else:
            line = content[pos:nl]
            line_end = nl + 1

        if terminator_pattern.match(line):
            return pos

        pos = line_end

    return len(content)


def _parse_working_memory_section(
    content: str
) -> Tuple[str, str, str, List[str]]:
    """
    Parse CLAUDE.md content to extract working memory section.

    Round 10 structural guarantee: the parser searches within the
    PACT-managed region only. This region contains only plugin-generated
    content (no user-authored fenced code blocks), so fence-aware scanning
    is unnecessary. If the managed region is not present (pre-migration
    file), falls back to scanning the full content. Returned slices
    (before_section, after_section) are always from the FULL content for
    correct write-back.

    Args:
        content: Full CLAUDE.md file content.

    Returns:
        Tuple of (before_section, section_header_with_comment, after_section, existing_entries)
        where existing_entries is a list of individual memory entry strings.
    """
    # Bound to managed region if available (round 10).
    region_result = extract_managed_region(content)
    if region_result is not None:
        scan_text, offset = region_result
    else:
        scan_text, offset = content, 0

    # Pattern to find the Working Memory section.
    # Negative lookahead excludes the three plugin-managed boundary prefixes
    # from being consumed as the auto-managed comment — otherwise an empty
    # Working Memory section followed immediately by <!-- PACT_MEMORY_END -->
    # would greedily swallow the marker (#404).
    section_pattern = re.compile(
        r'^(## Working Memory)\s*\n'
        rf'(<!-- (?!(?:{_PACT_BOUNDARY_ALT}))[^>]*-->)?\s*\n?',
        re.MULTILINE
    )

    match = section_pattern.search(scan_text)

    if not match:
        # Section doesn't exist
        return content, "", "", []

    section_start = match.start() + offset
    section_header_end = match.end()

    # Find where the next ## section starts (end of working memory section).
    # No fence-awareness needed — managed region contains only plugin-generated
    # content (round 10 structural guarantee).
    next_section_pattern = re.compile(
        rf'(#\s|##\s(?!Working Memory)|---|<!-- (?:{_PACT_BOUNDARY_ALT}))',
    )
    section_end_rel = _find_terminator_offset(
        scan_text, section_header_end, next_section_pattern
    )
    section_end = section_end_rel + offset

    before_section = content[:section_start]
    section_content = scan_text[section_header_end:section_end_rel].strip()
    after_section = content[section_end:]

    # Parse existing entries (each starts with ### YYYY-MM-DD)
    entry_pattern = re.compile(r'^### \d{4}-\d{2}-\d{2}', re.MULTILINE)
    entry_starts = [m.start() for m in entry_pattern.finditer(section_content)]

    existing_entries = []
    for i, start in enumerate(entry_starts):
        if i + 1 < len(entry_starts):
            entry = section_content[start:entry_starts[i + 1]].strip()
        else:
            entry = section_content[start:].strip()
        existing_entries.append(entry)

    return before_section, WORKING_MEMORY_HEADER, after_section, existing_entries


def sync_to_claude_md(
    memory: Dict[str, Any],
    files: Optional[List[str]] = None,
    memory_id: Optional[str] = None
) -> bool:
    """
    Sync a memory entry to the Working Memory section of CLAUDE.md.

    Maintains a rolling window of the last 3 memories. New entries are added
    at the top of the section, and entries beyond MAX_WORKING_MEMORIES are removed.

    This function is designed for graceful degradation - if CLAUDE.md doesn't
    exist or the sync fails for any reason, it logs a warning but doesn't
    raise an exception.

    Args:
        memory: Memory dictionary with context, goal, decisions, lessons_learned, etc.
        files: Optional list of file paths associated with this memory.
        memory_id: Optional memory ID to include for database reference.

    Returns:
        True if sync succeeded, False otherwise.
    """
    claude_md_path = _resolve_display_claude_md_path()

    if claude_md_path is None:
        logger.debug("CLAUDE.md not found, skipping working memory sync")
        return False

    try:
        # Serialize the FULL read-modify-write window under the shared sidecar
        # lock (see the "why lock the whole window" note above file_lock for the
        # read-under-lock / lock-identity / CLAUDE_PROJECT_DIR rationale).
        with file_lock(claude_md_path):
            # Read current content
            content = claude_md_path.read_text(encoding="utf-8")

            # Parse existing working memory section
            before_section, section_header, after_section, existing_entries = \
                _parse_working_memory_section(content)

            # Format new memory entry
            new_entry = _format_memory_entry(memory, files, memory_id)

            # Build new entries list: new entry first, then existing (up to max - 1)
            all_entries = [new_entry] + existing_entries
            trimmed_entries = all_entries[:MAX_WORKING_MEMORIES]

            # Apply token budget: compress older entries if over budget
            trimmed_entries = _apply_token_budget(
                trimmed_entries, WORKING_MEMORY_TOKEN_BUDGET
            )

            # Build new section content
            section_lines = [
                WORKING_MEMORY_HEADER,
                WORKING_MEMORY_COMMENT,
                ""  # Blank line after comment
            ]
            for entry in trimmed_entries:
                section_lines.append(entry)
                section_lines.append("")  # Blank line between entries

            section_text = "\n".join(section_lines)

            # Reconstruct file content
            if section_header:
                # Section existed, replace it
                new_content = before_section + section_text + after_section
            else:
                # Section didn't exist, append at end
                if not content.endswith("\n"):
                    content += "\n"
                new_content = content + "\n" + section_text

            # Write back to file (atomic: temp + rename, so a crash mid-write
            # cannot leave the always-loaded CLAUDE.md truncated)
            _atomic_write_text(claude_md_path, new_content)

        logger.info("Synced memory to CLAUDE.md Working Memory section")
        return True

    except Exception as e:
        logger.warning(f"Failed to sync memory to CLAUDE.md: {e}")
        return False


def _parse_retrieved_context_section(
    content: str
) -> Tuple[str, str, str, List[str]]:
    """
    Parse CLAUDE.md content to extract retrieved context section.

    Round 10 structural guarantee: same managed-region bounding as
    _parse_working_memory_section — see that function's docstring.

    Args:
        content: Full CLAUDE.md file content.

    Returns:
        Tuple of (before_section, section_header, after_section, existing_entries)
        where existing_entries is a list of individual memory entry strings.
    """
    # Bound to managed region if available (round 10).
    region_result = extract_managed_region(content)
    if region_result is not None:
        scan_text, offset = region_result
    else:
        scan_text, offset = content, 0

    # Pattern to find the Retrieved Context section.
    # Negative lookahead narrows to the plugin-managed boundary prefixes
    # — see _parse_working_memory_section for the full rationale (#404).
    section_pattern = re.compile(
        r'^(## Retrieved Context)\s*\n'
        rf'(<!-- (?!(?:{_PACT_BOUNDARY_ALT}))[^>]*-->)?\s*\n?',
        re.MULTILINE
    )

    match = section_pattern.search(scan_text)

    if not match:
        # Section doesn't exist
        return content, "", "", []

    section_start = match.start() + offset
    section_header_end = match.end()

    # Find where the next ## section starts (end of retrieved context section).
    # No fence-awareness needed — managed region contains only plugin-generated
    # content (round 10 structural guarantee).
    next_section_pattern = re.compile(
        rf'(#\s|##\s(?!Retrieved Context)|---|<!-- (?:{_PACT_BOUNDARY_ALT}))',
    )
    section_end_rel = _find_terminator_offset(
        scan_text, section_header_end, next_section_pattern
    )
    section_end = section_end_rel + offset

    before_section = content[:section_start]
    section_content = scan_text[section_header_end:section_end_rel].strip()
    after_section = content[section_end:]

    # Parse existing entries (each starts with ### YYYY-MM-DD)
    entry_pattern = re.compile(r'^### \d{4}-\d{2}-\d{2}', re.MULTILINE)
    entry_starts = [m.start() for m in entry_pattern.finditer(section_content)]

    existing_entries = []
    for i, start in enumerate(entry_starts):
        if i + 1 < len(entry_starts):
            entry = section_content[start:entry_starts[i + 1]].strip()
        else:
            entry = section_content[start:].strip()
        existing_entries.append(entry)

    return before_section, RETRIEVED_CONTEXT_HEADER, after_section, existing_entries


def _format_retrieved_entry(
    memory: Dict[str, Any],
    query: str,
    score: Optional[float] = None,
    memory_id: Optional[str] = None
) -> str:
    """
    Format a retrieved memory as a markdown entry for CLAUDE.md.

    Args:
        memory: Memory dictionary with context, goal, decisions, etc.
        query: The search query that retrieved this memory.
        score: Optional similarity score.
        memory_id: Optional memory ID for reference.

    Returns:
        Formatted markdown string for the retrieved entry.
    """
    # Get date and time for header
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d %H:%M")

    lines = [f"### {date_str}"]
    lines.append(f"**Query**: \"{query}\"")

    if score is not None:
        lines.append(f"**Relevance**: {score:.2f}")

    # Add context if present
    if memory.get("context"):
        # Truncate long context for display
        context = memory['context']
        if len(context) > 200:
            context = context[:197] + "..."
        lines.append(f"**Context**: {context}")

    # Add goal if present
    if memory.get("goal"):
        lines.append(f"**Goal**: {memory['goal']}")

    # Add memory ID if provided
    if memory_id:
        lines.append(f"**Memory ID**: {memory_id}")

    return "\n".join(lines)


def sync_retrieved_to_claude_md(
    memories: List[Dict[str, Any]],
    query: str,
    scores: Optional[List[float]] = None,
    memory_ids: Optional[List[str]] = None
) -> bool:
    """
    Sync retrieved memories to the Retrieved Context section of CLAUDE.md.

    Maintains a rolling window of the last 3 retrieved memories. New entries
    are added at the top of the section, and entries beyond MAX_RETRIEVED_MEMORIES
    are removed.

    Args:
        memories: List of memory dictionaries that were retrieved.
        query: The search query used.
        scores: Optional list of similarity scores (same order as memories).
        memory_ids: Optional list of memory IDs (same order as memories).

    Returns:
        True if sync succeeded, False otherwise.
    """
    if not memories:
        return False

    claude_md_path = _resolve_display_claude_md_path()

    if claude_md_path is None:
        logger.debug("CLAUDE.md not found, skipping retrieved context sync")
        return False

    try:
        # Serialize the FULL read-modify-write window under the shared sidecar
        # lock (see the "why lock the whole window" note above file_lock for the
        # read-under-lock / lock-identity / CLAUDE_PROJECT_DIR rationale).
        with file_lock(claude_md_path):
            # Read current content
            content = claude_md_path.read_text(encoding="utf-8")

            # Parse existing retrieved context section
            before_section, section_header, after_section, existing_entries = \
                _parse_retrieved_context_section(content)

            # Format new entries (only the top result to avoid clutter)
            new_entries = []
            top_memory = memories[0]
            score = scores[0] if scores else None
            memory_id = memory_ids[0] if memory_ids else None
            new_entry = _format_retrieved_entry(top_memory, query, score, memory_id)
            new_entries.append(new_entry)

            # Build new entries list: new entry first, then existing (up to max - 1)
            all_entries = new_entries + existing_entries
            trimmed_entries = all_entries[:MAX_RETRIEVED_MEMORIES]

            # Apply token budget: reduce entry count if over budget.
            # Retrieved entries are already compact (~200 chars each), drop oldest rather than compress.
            # Subtract the popped entry's tokens instead of recalculating the full sum.
            total_tokens = sum(_estimate_tokens(e) for e in trimmed_entries)
            while len(trimmed_entries) > 1 and total_tokens > RETRIEVED_CONTEXT_TOKEN_BUDGET:
                removed = trimmed_entries.pop()
                total_tokens -= _estimate_tokens(removed)

            # Build new section content
            section_lines = [
                RETRIEVED_CONTEXT_HEADER,
                RETRIEVED_CONTEXT_COMMENT,
                ""  # Blank line after comment
            ]
            for entry in trimmed_entries:
                section_lines.append(entry)
                section_lines.append("")  # Blank line between entries

            section_text = "\n".join(section_lines)

            # Reconstruct file content
            if section_header:
                # Section existed, replace it
                # Ensure blank line before next section
                if after_section and not after_section.startswith("\n"):
                    new_content = before_section + section_text + "\n" + after_section
                else:
                    new_content = before_section + section_text + after_section
            else:
                # Section didn't exist, insert before Working Memory if it exists
                working_memory_match = re.search(
                    r'^## Working Memory',
                    content,
                    re.MULTILINE
                )
                if working_memory_match:
                    # Insert before Working Memory with blank line
                    insert_pos = working_memory_match.start()
                    new_content = content[:insert_pos] + section_text + "\n" + content[insert_pos:]
                else:
                    # Append at end
                    if not content.endswith("\n"):
                        content += "\n"
                    new_content = content + "\n" + section_text

            # Write back to file (atomic: temp + rename, so a crash mid-write
            # cannot leave the always-loaded CLAUDE.md truncated)
            _atomic_write_text(claude_md_path, new_content)

        logger.info("Synced retrieved memories to CLAUDE.md Retrieved Context section")
        return True

    except Exception as e:
        logger.warning(f"Failed to sync retrieved memories to CLAUDE.md: {e}")
        return False
