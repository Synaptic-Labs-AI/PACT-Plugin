"""
Location: pact-plugin/hooks/shared/claude_md_manager.py
Summary: CLAUDE.md file manipulation for PACT environment setup.
Used by: session_init.py during SessionStart hook to scaffold the project
         CLAUDE.md PACT_MANAGED region (outer boundary, session block,
         PACT_MEMORY-wrapped memory sections) and to migrate legacy project
         CLAUDE.md files into the boundary structure.

Manages the project CLAUDE.md at $CLAUDE_PROJECT_DIR — preferred at
.claude/CLAUDE.md, legacy at ./CLAUDE.md — with the PACT_MANAGED outer
boundary, optional SESSION_START/SESSION_END block, and PACT_MEMORY inner
boundary wrapping memory sections.

Project CLAUDE.md location resolution:
Claude Code supports two locations for project-level memory:
  - $CLAUDE_PROJECT_DIR/.claude/CLAUDE.md  (preferred / new default)
  - $CLAUDE_PROJECT_DIR/CLAUDE.md          (legacy)
The resolve_project_claude_md_path() helper picks whichever exists, with
.claude/CLAUDE.md taking priority. When neither exists, it returns the new
default path so creators land at the preferred location.
"""

import fcntl  # Unix-only; PACT supports macOS/Linux. No Windows compat shim.
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

# Project-level CLAUDE.md is preferred at .claude/CLAUDE.md (the new default)
# but Claude Code also accepts ./CLAUDE.md for backwards compatibility.
_DOT_CLAUDE_RELATIVE = Path(".claude") / "CLAUDE.md"
_LEGACY_RELATIVE = Path("CLAUDE.md")

# Concurrency guard: callers performing read-mutate-write on managed
# CLAUDE.md files (ensure_project_memory_md, migrate_to_managed_structure,
# session_resume.update_session_info) acquire this lock to prevent two
# concurrent session_init hooks (e.g., resuming session A while starting
# session B on the same project) from interleaving and clobbering each
# other's writes. A sidecar lock (`.{filename}.lock` adjacent to the
# target) serializes the critical sections.
#
# Sidecar is chosen over direct target-file locking because:
#   1. The target file may be recreated (rename/delete) during the write; a
#      sidecar lock file is independent of the target's inode lifetime.
#   2. Locking the target itself would interleave with its own read/write.
#   3. Sidecar is standard UNIX practice for cross-process coordination.
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_INTERVAL = 0.1


@contextmanager
def file_lock(target_file: Path):
    """Acquire an exclusive sidecar file lock for a target CLAUDE.md path.

    Not re-entrant: nested acquisition from the same thread will deadlock
    (TimeoutError after ``_LOCK_TIMEOUT_SECONDS``).

    Creates (or opens) `{target_file.parent}/.{target_file.name}.lock` and
    takes an ``fcntl`` exclusive advisory lock on its file descriptor.
    Polls with non-blocking acquire + sleep so a stuck holder cannot hang
    session_init forever: raises ``TimeoutError`` after
    ``_LOCK_TIMEOUT_SECONDS``.

    The lock file is intentionally NOT cleaned up on exit. Stale lock files
    are cheap (an empty byte-0 file per managed target), and removing the
    sidecar inside the lock window is a classic race: another waiter may
    have already opened the same path and would be locking a now-orphaned
    inode. Leaving the file in place is correct and safe.

    Args:
        target_file: The managed CLAUDE.md path whose read-mutate-write
            section must be serialized. Must have an existing parent
            directory (caller ensures this); this function does not
            create parents for the target, only for the sidecar lock.

    Raises:
        TimeoutError: Lock not acquired within the timeout window. Caller
            should treat this as a transient failure and return a
            fail-open status string so session_init can surface it.
    """
    lock_path = target_file.parent / f".{target_file.name}.lock"
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

# Outer boundary wrapping all PACT-managed content in project CLAUDE.md.
# User-owned content goes OUTSIDE this block.
MANAGED_START_MARKER = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
MANAGED_END_MARKER = "<!-- PACT_MANAGED_END -->"

# Inner boundary wrapping project memory sections (Retrieved Context,
# Pinned Context, Working Memory) for hook targeting (#404).
MEMORY_START_MARKER = "<!-- PACT_MEMORY_START -->"
MEMORY_END_MARKER = "<!-- PACT_MEMORY_END -->"

# Canonical H1 title for the managed block. Extracted as a constant so
# the three template sites (ensure_project_memory_md, _build_migrated_content,
# session_resume.update_session_info Case 0) cannot drift apart. Changing this
# value changes the title everywhere in one place.
MANAGED_TITLE = "# PACT Framework and Managed Project Memory"

# Plugin-managed HTML comment boundary prefixes. Used by parsers and regex
# sites that need to terminate scans on any PACT-managed boundary marker.
# Extracted as a constant so the three-prefix union is defined once.
#
# Twin copy: working_memory.py maintains a parallel _PACT_BOUNDARY_PREFIXES
# tuple because skills/pact-memory/scripts/ cannot cleanly import from
# hooks/shared/. A drift-detection test asserts the two tuples stay in sync.
PACT_BOUNDARY_PREFIXES: tuple[str, ...] = (
    "PACT_MEMORY_",
    "PACT_MANAGED_",
    "PACT_ROUTING_",
)

# Regex alternation used by scan-terminator patterns in this module.
# Mirrors the `_BOUNDARY_ALT` constant in `staleness.py`:
# any regex that needs to terminate on a PACT boundary marker must embed
# this alternation rather than hard-coding the three-prefix literal. That
# way, adding a fourth prefix to `PACT_BOUNDARY_PREFIXES` automatically
# picks it up everywhere via a one-line constant change.
_BOUNDARY_ALT = "|".join(PACT_BOUNDARY_PREFIXES)

# Stale line from the legacy project CLAUDE.md template. The line lingers
# in upgraded files; strip it during migration. Allows optional trailing
# period / whitespace.
#
# This pattern is applied per-line by `_strip_legacy_lines` via a
# fence-aware walker, NOT module-wide with `re.MULTILINE`. The per-line
# form is anchored to the full stripped line, so `$` matches end-of-line
# without needing a MULTILINE flag. Removing MULTILINE is load-bearing:
# with MULTILINE the pattern was hot inside user-authored fenced code
# blocks and silently destroyed example content that quoted the stale
# template line. Per-line application + fence tracking prevents that
# failure mode entirely.
_STALE_ORCHESTRATOR_LINE_RE = re.compile(
    r"^The global PACT Orchestrator is loaded from `~/\.claude/CLAUDE\.md`\.?\s*$",
)


def _strip_legacy_lines(content: str) -> str:
    r"""
    Remove lines from older PACT template versions that are now obsolete.

    Currently strips the stale orchestrator-loader line from the legacy
    project CLAUDE.md template. Used by `_build_migrated_content` during
    project migration. Centralizing the set of legacy-line patterns here
    means adding a new pattern in the future only requires editing this
    helper.

    PR #404: fence-aware line walker that applies
    `_STALE_ORCHESTRATOR_LINE_RE` ONLY to lines that are NOT inside a
    fenced code block. Lines inside a fence are preserved verbatim, even
    if they match the stale-line regex. This prevents silent data loss when
    a user's CLAUDE.md contains a fenced code block that quotes the legacy
    template verbatim (e.g., migration documentation, tutorial content).

    Supports both backtick (```) and tilde (~~~) fences as independent
    fence types per CommonMark §4.5. A line inside a backtick fence that
    contains ~~~ does not affect tilde state (and vice versa).

    Prior behavior used `re.MULTILINE` on the whole content, which stripped
    matching lines regardless of fence state, silently destroying fenced
    example content. Per-line application plus fence tracking fixes this.

    Args:
        content: The raw CLAUDE.md content to scrub.

    Returns:
        Content with all legacy template lines OUTSIDE fenced code blocks
        removed. Content inside fenced code blocks (backtick or tilde) is
        preserved byte for byte. Pure function.
    """
    # PR #404: length-tracked fence state per CommonMark §4.5 — closing
    # fence must use the same character and run length >= the opening. A
    # 4-backtick outer fence containing a 3-backtick inner example must
    # NOT toggle state on the inner line. fence_open_len > 0 means we're
    # inside a fence; fence_char records which character opened it. This
    # is the only fence walker that remains after the structural
    # simplification (it processes user content during migration).
    pos = 0
    fence_open_len = 0  # 0 = not inside a fence
    fence_char = ""     # "`" or "~" when inside a fence
    out_parts: list[str] = []
    while pos < len(content):
        nl = content.find("\n", pos)
        if nl == -1:
            line = content[pos:]
            raw_segment = line
            line_end = len(content)
        else:
            line = content[pos:nl]
            raw_segment = content[pos:nl + 1]
            line_end = nl + 1

        stripped = line.lstrip()

        if fence_open_len == 0:
            # Not inside a fence — check for fence open
            if stripped.startswith("```"):
                run_len = len(stripped) - len(stripped.lstrip("`"))
                fence_open_len = run_len
                fence_char = "`"
                out_parts.append(raw_segment)
            elif stripped.startswith("~~~"):
                run_len = len(stripped) - len(stripped.lstrip("~"))
                fence_open_len = run_len
                fence_char = "~"
                out_parts.append(raw_segment)
            elif _STALE_ORCHESTRATOR_LINE_RE.match(line):
                # Non-fenced legacy line: drop it entirely
                pass
            else:
                out_parts.append(raw_segment)
        else:
            # Inside a fence — check for fence close (same char, run >= open)
            if fence_char == "`" and stripped.startswith("```"):
                run_len = len(stripped) - len(stripped.lstrip("`"))
                # Close only if the line is ONLY fence chars (+ optional
                # trailing whitespace). CommonMark §4.5: closing fence
                # cannot have info string.
                after_run = stripped[run_len:].strip()
                if run_len >= fence_open_len and not after_run:
                    fence_open_len = 0
                    fence_char = ""
            elif fence_char == "~" and stripped.startswith("~~~"):
                run_len = len(stripped) - len(stripped.lstrip("~"))
                after_run = stripped[run_len:].strip()
                if run_len >= fence_open_len and not after_run:
                    fence_open_len = 0
                    fence_char = ""
            # Keep fence body verbatim regardless
            out_parts.append(raw_segment)

        pos = line_end

    return "".join(out_parts)



def strip_orphan_kernel_block() -> str | None:
    """
    SUNSET BEFORE v5.0.0: one-version-window migration helper.

    Strips the obsolete `<!-- PACT_START:... -->...<!-- PACT_END -->` kernel
    block from `~/.claude/CLAUDE.md` if present. The block was injected by
    pre-v4.0 plugin versions that delivered the orchestrator persona via
    home-dir CLAUDE.md routing; v4.0+ delivers the persona via the
    `claude --agent` flag instead, so the block is now stale.

    Called from session_init.py on every SessionStart. Idempotent no-op
    when the markers are absent (i.e., for fresh installs or after first
    cleanup). Once the v4.0.0 release has been in the field long enough
    that resumed users will have hit at least one v4.x SessionStart, this
    function and its caller can be deleted.

    Renamed from the v3.x `remove_stale_kernel_block` to mirror the
    `strip_orphan_*` naming used by the sibling project-CLAUDE.md cleanup
    in session_init.py.

    Hardening:
    - Symlink guard inside the lock (TOCTOU defense): refuses to operate
      if `~/.claude/CLAUDE.md` is a symlink. Practical exploitability is
      low (requires pre-existing local write access) but the defensive
      guard is cheap.
    - Malformed-pair feedback: when the migration skips due to a malformed
      marker state (orphan marker or END-before-START), returns the warning
      as a status string so session_init.py surfaces it via systemMessage.
      Hook stderr is NOT shown to users by Claude Code, so a returned
      string is the only way to deliver the warning.

    Returns:
        Status message on successful removal, None on no-op (clean,
        absent markers) or error, or a "Migration skipped: ..." string
        on defensive no-op (malformed marker state; session_init.py
        routes these to systemMessages via the "failed"/"skipped" check).
    """
    target_file = Path.home() / ".claude" / "CLAUDE.md"
    if not target_file.exists():
        return None

    # Concurrency guard: serialize read-mutate-write so two concurrent
    # session_init hooks on the same home file cannot clobber each other.
    # Fail-open on timeout — next session start will retry.
    try:
        with file_lock(target_file):
            # Symlink guard INSIDE the lock (TOCTOU defense): is_symlink
            # uses lstat under the hood which does NOT follow the link, so
            # this is safe even if the link target is itself a malicious
            # file. Inside the lock so an attacker cannot swap the target
            # between an outside-lock check and the write.
            if target_file.is_symlink():
                return (
                    "Migration skipped: ~/.claude/CLAUDE.md path "
                    "precondition not met."
                )

            try:
                content = target_file.read_text(encoding="utf-8")
            except OSError:
                return None

            START_MARKER = "<!-- PACT_START:"
            END_MARKER = "<!-- PACT_END -->"

            has_start = START_MARKER in content
            has_end = END_MARKER in content

            if not has_start and not has_end:
                # Normal idempotent no-op for already-migrated installs.
                return None

            if has_start != has_end:
                # Only one of the two markers is present. Defensive no-op
                # to avoid data loss; surface a status string so
                # session_init.py routes it via systemMessage. This case
                # can occur if a prior plugin write crashed mid-file or
                # the user manually deleted one marker.
                which = "PACT_START" if has_start else "PACT_END"
                missing = "PACT_END" if has_start else "PACT_START"
                return (
                    f"Migration skipped: ~/.claude/CLAUDE.md contains "
                    f"{which} but no matching {missing}. To avoid data "
                    f"loss, inspect the file and either remove the "
                    f"orphan {which} marker or restore the matching "
                    f"{missing} marker."
                )

            pre_marker, rest = content.split(START_MARKER, 1)
            if END_MARKER not in rest:
                # END marker exists in content but appears textually
                # before START. Same defensive handling.
                return (
                    "Migration skipped: ~/.claude/CLAUDE.md contains "
                    "both PACT_START and PACT_END markers but PACT_END "
                    "appears before PACT_START. Inspect the file and "
                    "reorder or remove the orphan markers."
                )

            _, post_marker = rest.split(END_MARKER, 1)

            # Preserve one blank line at the removal boundary so the
            # user's spacing around the obsolete block survives the strip.
            pre_clean = pre_marker.rstrip("\r\n")
            post_clean = post_marker.lstrip("\r\n")
            if pre_clean and post_clean:
                new_content = pre_clean + "\n\n" + post_clean
            elif pre_clean:
                new_content = pre_clean + "\n"
            elif post_clean:
                new_content = post_clean
            else:
                new_content = ""

            try:
                target_file.write_text(new_content, encoding="utf-8")
                os.chmod(str(target_file), 0o600)
                return (
                    "Removed obsolete PACT kernel block from "
                    "~/.claude/CLAUDE.md"
                )
            except OSError as e:
                return (
                    f"Failed to remove stale kernel block: {str(e)[:50]}"
                )
    except TimeoutError:
        return (
            "Failed to acquire lock on ~/.claude/CLAUDE.md within 5s "
            "(another session_init hook may be running concurrently). "
            "Kernel-block migration skipped; will retry on next session "
            "start."
        )


def extract_managed_region(content: str) -> tuple[str, int] | None:
    """
    Extract the PACT-managed region from a CLAUDE.md file.

    Returns the content between MANAGED_START_MARKER and MANAGED_END_MARKER
    (exclusive of the markers themselves), or None if either marker is missing.

    The managed region contains only plugin-generated content — no user-authored
    fenced code blocks. This is the structural guarantee that makes fence-aware
    parsing unnecessary for consumers that operate within the managed region.

    Args:
        content: Full CLAUDE.md file content.

    Returns:
        Tuple of (region_text, start_offset) where start_offset is the absolute
        byte offset of the first character after MANAGED_START_MARKER in the
        original content. Callers that need to write back to the full file must
        add start_offset to any positions computed within region_text.
        Returns None if either marker is missing.
    """
    start_idx = content.find(MANAGED_START_MARKER)
    if start_idx == -1:
        return None
    region_start = start_idx + len(MANAGED_START_MARKER)
    end_idx = content.find(MANAGED_END_MARKER, region_start)
    if end_idx == -1:
        return None
    return content[region_start:end_idx], region_start


def resolve_project_claude_md_path(
    project_dir: str | os.PathLike[str],
) -> tuple[Path, str]:
    """
    Resolve the project-level CLAUDE.md path with dual-location support.

    Detection priority:
      1. $project_dir/.claude/CLAUDE.md   -> ("dot_claude", existing)
      2. $project_dir/CLAUDE.md           -> ("legacy", existing)
      3. Neither exists                    -> ("new_default", .claude/CLAUDE.md)

    Callers that only read use the returned Path directly. Callers that
    create the file use the source string to know whether they need to
    `mkdir` the `.claude/` parent directory first.

    Args:
        project_dir: The CLAUDE_PROJECT_DIR root.

    Returns:
        Tuple of (path, source) where source is one of:
          - "dot_claude": existing .claude/CLAUDE.md
          - "legacy": existing ./CLAUDE.md
          - "new_default": neither exists; path points to .claude/CLAUDE.md
            so a creator can write to the preferred location.
    """
    base = Path(project_dir)
    dot_claude = base / _DOT_CLAUDE_RELATIVE
    legacy = base / _LEGACY_RELATIVE

    if dot_claude.exists():
        return dot_claude, "dot_claude"
    if legacy.exists():
        return legacy, "legacy"
    return dot_claude, "new_default"


def ensure_dot_claude_parent(path: Path) -> None:
    """
    Ensure the parent directory of a `.claude/CLAUDE.md` path exists.

    No-op when the parent already exists as a directory. Creates the
    directory with mode 0o700 to match the rest of the PACT plugin's
    secure-by-default file permissions. Safe to call for any CLAUDE.md
    path -- if the parent is not a `.claude` dir, this is just an
    existence check.

    Raises early with a clear message when the parent path exists but is
    a regular file (e.g., a local attacker deliberately blocking mkdir
    by creating a file where `.claude/` should be). Without this guard
    the code path would fall through to the subsequent write_text call
    and surface a less-clear late-stage OSError.

    Args:
        path: The target CLAUDE.md path (e.g. /proj/.claude/CLAUDE.md).

    Raises:
        OSError: When `path.parent` exists but is not a directory. The
            caller (ensure_project_memory_md) catches OSError and
            returns a user-facing failure status string.
    """
    parent = path.parent
    if parent.exists() and not parent.is_dir():
        raise OSError(f"{parent} exists but is not a directory")
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def ensure_project_memory_md() -> str | None:
    """
    Ensure project has a CLAUDE.md with memory sections.

    Creates a minimal project-level CLAUDE.md containing the PACT-managed
    structure: outer PACT_MANAGED boundary, session block, and inner
    PACT_MEMORY boundary wrapping memory sections (Retrieved Context,
    Pinned Context, Working Memory) if one doesn't exist. These sections
    are project-specific and managed by the pact-memory skill.

    Honors both supported project CLAUDE.md locations:
      - $CLAUDE_PROJECT_DIR/.claude/CLAUDE.md  (preferred / new default)
      - $CLAUDE_PROJECT_DIR/CLAUDE.md          (legacy)
    If either exists, no action is taken (preserves existing project
    configuration). When neither exists, creates the file at the preferred
    `.claude/CLAUDE.md` location, creating the `.claude/` directory if needed.

    Returns:
        Status message or None if no action taken.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    target_file, source = resolve_project_claude_md_path(project_dir)

    # Don't overwrite existing project CLAUDE.md (either location)
    if source != "new_default":
        return None

    # Create minimal CLAUDE.md with memory sections at the new default location.
    # Structure (#404): outer PACT_MANAGED boundary wraps all plugin-managed
    # content; inner PACT_MEMORY boundary wraps the memory sections.
    memory_template = f"""{MANAGED_START_MARKER}
{MANAGED_TITLE}

<!-- SESSION_START -->
## Current Session
<!-- Auto-managed by session_init hook. Overwritten each session. -->
<!-- SESSION_END -->

{MEMORY_START_MARKER}
## Retrieved Context
<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->

## Pinned Context

## Working Memory
<!-- Auto-managed by pact-memory skill. Last 3 memories shown. Full history searchable via pact-memory skill. -->
{MEMORY_END_MARKER}

{MANAGED_END_MARKER}
"""

    # Concurrency guard: serialize symlink check + write so two concurrent
    # session_init hooks on the same project cannot both see "new_default"
    # and race on the write. Fail-open on timeout — next session start retries.
    try:
        ensure_dot_claude_parent(target_file)
        with file_lock(target_file):
            # Symlink guard: the resolver returned "new_default" (neither
            # location exists), but the preferred path could still be a
            # dangling symlink. is_symlink uses lstat and returns True even
            # for dangling links. Re-check inside the lock so a concurrent
            # writer that just created the file is detected.
            if target_file.is_symlink():
                return "Project CLAUDE.md skipped: path precondition not met."
            if target_file.exists():
                return None
            try:
                target_file.write_text(memory_template, encoding="utf-8")
                os.chmod(str(target_file), 0o600)
                return "Created project CLAUDE.md with memory sections"
            except OSError as e:
                return f"Project CLAUDE.md failed: {str(e)[:50]}"
    except TimeoutError:
        return (
            "Failed to acquire lock on project CLAUDE.md within 5s "
            "(another session_init hook may be running concurrently). "
            "Project CLAUDE.md creation skipped; will retry on next session start."
        )
    except OSError as e:
        return f"Project CLAUDE.md failed: {str(e)[:50]}"


def migrate_to_managed_structure() -> str | None:
    """
    One-time migration: wrap existing project CLAUDE.md content in the
    PACT_MANAGED boundary and add PACT_MEMORY markers around memory sections.

    Called from session_init.py on every SessionStart. Idempotent no-op when
    PACT_MANAGED_START marker is already present. Follows the same hardening
    pattern as the other managed-file writers: file_lock, symlink guard inside
    the lock, fail-open on timeout/error.

    Idempotency guard: if PACT_MANAGED_START is already present, the
    function returns None without touching the file.

    Migration strategy (applied when the guard passes):
    1. Locate the existing sections by their markers/headings:
       - PACT_ROUTING block (between PACT_ROUTING_START/END)
       - SESSION block (between SESSION_START/END)
       - Memory sections: "## Retrieved Context", "## Pinned Context",
         "## Working Memory"
    2. Replace the legacy "# Project Memory" heading with the single canonical
       H1 "# PACT Framework and Managed Project Memory"
    3. Wrap memory sections in PACT_MEMORY_START/END (always emitting all
       three canonical H2 headings, even if some were absent in the source)
    4. Wrap the entire managed region in PACT_MANAGED_START/END; content
       outside the recognized PACT sections is preserved AFTER the closing
       boundary as user-owned content

    User content with fenced code blocks containing ## memory headings is
    preserved verbatim. The classifier tracks in_code_fence state and does
    not misclassify fence-protected headings as real memory sections
    (PR #404).

    Returns:
        Status message on successful migration, None on no-op (already
        migrated or file doesn't exist), or a "failed"/"skipped" string
        on error (routed to systemMessages by session_init.py).
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    target_file, source = resolve_project_claude_md_path(project_dir)

    if source == "new_default":
        return None  # File doesn't exist; ensure_project_memory_md() handles creation

    try:
        with file_lock(target_file):
            if target_file.is_symlink():
                return "Migration skipped: project CLAUDE.md path precondition not met."

            try:
                content = target_file.read_text(encoding="utf-8")
            except OSError:
                return None

            # Idempotent guard: already migrated
            if MANAGED_START_MARKER in content:
                return None

            new_content = _build_migrated_content(content)

            try:
                target_file.write_text(new_content, encoding="utf-8")
                os.chmod(str(target_file), 0o600)
                return "Migrated project CLAUDE.md to managed structure (#404)"
            except OSError as e:
                return f"Migration failed: {str(e)[:50]}"
    except TimeoutError:
        return (
            "Failed to acquire lock on project CLAUDE.md within 5s "
            "(another session_init hook may be running concurrently). "
            "CLAUDE.md migration skipped; will retry on next session start."
        )




def _build_migrated_content(content: str) -> str:
    """
    Transform old-format CLAUDE.md content into the new managed structure.

    Extracts the PACT-managed sections (routing, session, memory) from the
    existing content and reassembles them inside the new boundary markers.
    Any content that falls outside the recognized PACT sections is preserved
    AFTER the PACT_MANAGED_END marker as user-owned content.

    This is a pure function (no I/O) for testability.

    Idempotency guard: if the content already contains MANAGED_START_MARKER,
    return it unchanged.

    User content that appears ABOVE the first PACT-managed section heading
    in the original file is classified as user_parts and lands BELOW
    PACT_MANAGED_END after migration. The single-region layout keeps every
    downstream parser fence-unaware.

    Args:
        content: The existing CLAUDE.md file content.

    Returns:
        The restructured content with PACT_MANAGED and PACT_MEMORY boundaries,
        or the original content unchanged if already migrated.
    """
    # Idempotency guard: already migrated → no-op
    if MANAGED_START_MARKER in content:
        return content

    # Extract session block if present (between markers)
    session_block = ""
    content_sans_routing = content
    content_sans_session = content_sans_routing
    session_start = "<!-- SESSION_START -->"
    session_end = "<!-- SESSION_END -->"
    if session_start in content_sans_routing and session_end in content_sans_routing:
        pattern = re.compile(
            re.escape(session_start) + r".*?" + re.escape(session_end),
            re.DOTALL,
        )
        match = pattern.search(content_sans_routing)
        if match:
            session_block = match.group(0)
            content_sans_session = (
                content_sans_routing[:match.start()]
                + content_sans_routing[match.end():]
            )

    # What remains after extracting routing + session is candidate for
    # memory sections and user content.
    remaining = content_sans_session

    # Remove the old top-level heading and description line
    remaining = re.sub(
        r"^# Project Memory\s*\n"
        r"(?:\s*\n)*"
        r"(?:This file contains project-specific memory managed by the PACT framework\.\s*\n)?",
        "",
        remaining,
    )

    # Strip legacy template lines (e.g., stale orchestrator-loader line)
    remaining = _strip_legacy_lines(remaining)

    # Extract memory sections: Retrieved Context, Pinned Context, Working Memory
    memory_headings = ["## Retrieved Context", "## Pinned Context", "## Working Memory"]
    memory_parts = []
    user_parts = []

    lines = remaining.splitlines(keepends=True)
    current_section: list[str] = []
    in_memory_section = False
    # Length-tracked fence state (PR #404): CommonMark §4.5 requires a
    # closing fence to use the same character and run length >= the opening.
    # A boolean toggle fails on tilde fences and 4+ backtick nesting. This
    # mirrors the model in _strip_legacy_lines.
    fence_open_len = 0  # 0 = not inside a fence
    fence_char = ""     # "`" or "~" when inside a fence

    for line in lines:
        stripped = line.rstrip()
        lstripped = stripped.lstrip()
        if fence_open_len == 0:
            # Not inside a fence — check for fence open
            if lstripped.startswith("```"):
                run_len = len(lstripped) - len(lstripped.lstrip("`"))
                fence_open_len = run_len
                fence_char = "`"
                current_section.append(line)
                continue
            elif lstripped.startswith("~~~"):
                run_len = len(lstripped) - len(lstripped.lstrip("~"))
                fence_open_len = run_len
                fence_char = "~"
                current_section.append(line)
                continue
        else:
            # Inside a fence — check for fence close (same char, run >= open)
            if fence_char == "`" and lstripped.startswith("```"):
                run_len = len(lstripped) - len(lstripped.lstrip("`"))
                after_run = lstripped[run_len:].strip()
                if run_len >= fence_open_len and not after_run:
                    fence_open_len = 0
                    fence_char = ""
            elif fence_char == "~" and lstripped.startswith("~~~"):
                run_len = len(lstripped) - len(lstripped.lstrip("~"))
                after_run = lstripped[run_len:].strip()
                if run_len >= fence_open_len and not after_run:
                    fence_open_len = 0
                    fence_char = ""
            # Keep fence body verbatim regardless
            current_section.append(line)
            continue
        if any(stripped == h for h in memory_headings):
            if current_section and not in_memory_section:
                user_parts.extend(current_section)
                current_section = []
            elif current_section and in_memory_section:
                memory_parts.extend(current_section)
                current_section = []
            in_memory_section = True
            current_section.append(line)
        elif stripped.startswith("## ") or stripped.startswith("# "):
            if current_section:
                if in_memory_section:
                    memory_parts.extend(current_section)
                else:
                    user_parts.extend(current_section)
                current_section = []
            in_memory_section = False
            current_section.append(line)
        else:
            current_section.append(line)

    if current_section:
        if in_memory_section:
            memory_parts.extend(current_section)
        else:
            user_parts.extend(current_section)

    memory_text = "".join(memory_parts).strip()
    user_text = "".join(user_parts).strip()

    # Split memory into {heading: body} dict — always emit all 3 headings.
    memory_sections: dict[str, str] = {
        "## Retrieved Context": "",
        "## Pinned Context": "",
        "## Working Memory": "",
    }

    def _append_body(heading: str, new_body: str) -> None:
        existing = memory_sections[heading]
        if existing and new_body:
            memory_sections[heading] = existing + "\n" + new_body
        elif new_body:
            memory_sections[heading] = new_body

    if memory_text:
        current_heading: str | None = None
        current_body: list[str] = []
        for line in memory_text.splitlines(keepends=True):
            stripped_line = line.rstrip()
            if stripped_line in memory_sections:
                if current_heading is not None:
                    _append_body(current_heading, "".join(current_body).rstrip())
                current_heading = stripped_line
                current_body = []
            elif current_heading is not None:
                current_body.append(line)
        if current_heading is not None:
            _append_body(current_heading, "".join(current_body).rstrip())

    # Build the new structure — all content goes inside the managed block
    parts: list[str] = []
    parts.extend([MANAGED_START_MARKER, "\n", f"{MANAGED_TITLE}\n"])

    if session_block:
        parts.extend(["\n", session_block, "\n"])

    parts.extend(["\n", MEMORY_START_MARKER, "\n"])
    heading_chunks: list[str] = []
    for heading in ("## Retrieved Context", "## Pinned Context", "## Working Memory"):
        body = memory_sections[heading]
        if body:
            heading_chunks.append(f"{heading}\n{body}\n")
        else:
            heading_chunks.append(f"{heading}\n")
    parts.append("\n".join(heading_chunks))
    if not parts[-1].endswith("\n"):
        parts.append("\n")
    parts.extend([MEMORY_END_MARKER, "\n"])

    parts.extend(["\n", MANAGED_END_MARKER, "\n"])

    if user_text:
        parts.extend(["\n", user_text, "\n"])

    return "".join(parts)


def match_project_claude_md(file_path_str: str) -> Path | None:
    """Match a tool-input file_path against the canonical project CLAUDE.md.

    Returns the canonical resolved path if `file_path_str` points at the
    project's CLAUDE.md (either `.claude/CLAUDE.md` or the legacy
    `./CLAUDE.md`), otherwise None. Intended for PreToolUse gates that
    need to short-circuit on non-CLAUDE.md targets.

    Relative `file_path_str` values are anchored against
    CLAUDE_PROJECT_DIR (Back-M3/Sec-F4): `Path.resolve()` on a relative
    path uses cwd, and a hook's cwd can drift (worktree switches,
    subprocess invocations). The env var is the stable anchor the plugin
    sets on every session. If CLAUDE_PROJECT_DIR is unset, relative
    input returns None — safer than a silent cwd dependency.

    Worktree-safe: imports `staleness.get_project_claude_md_path` lazily
    to avoid circular-import and module-load cost on every Edit/Write.
    That function already handles env-var / git-root / cwd fallbacks.

    Fail-safe: any OSError / RuntimeError while resolving returns None.
    Callers treat None as "not our target; let the tool through."
    """
    if not file_path_str:
        return None

    try:
        from staleness import get_project_claude_md_path
    except ImportError:
        return None

    project_md = get_project_claude_md_path()
    if project_md is None:
        return None

    try:
        target_path = Path(file_path_str)
        if not target_path.is_absolute():
            project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
            if not project_dir:
                return None
            target_path = Path(project_dir) / target_path
        target = target_path.resolve()
        canonical = project_md.resolve()
    except (OSError, RuntimeError):
        return None

    if target != canonical:
        return None
    return canonical
