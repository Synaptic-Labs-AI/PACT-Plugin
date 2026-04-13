"""
Location: pact-plugin/hooks/shared/claude_md_manager.py
Summary: CLAUDE.md file manipulation for PACT environment setup.
Used by: session_init.py during SessionStart hook to migrate any obsolete
         kernel block out of the home CLAUDE.md, keep the project CLAUDE.md
         routing block canonical, and migrate legacy project CLAUDE.md files
         to the PACT_MANAGED boundary structure (#404).

Manages two CLAUDE.md files:
1. ~/.claude/CLAUDE.md -- one-time migration: strip the obsolete
   PACT_START/PACT_END kernel block left over from prior plugin versions.
2. {project}/CLAUDE.md -- project-level file (at .claude/CLAUDE.md preferred,
   or legacy ./CLAUDE.md) with PACT_MANAGED boundary, routing block, session
   block, and PACT_MEMORY-wrapped memory sections.

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
import time
from contextlib import contextmanager
from pathlib import Path

# Project-level CLAUDE.md is preferred at .claude/CLAUDE.md (the new default)
# but Claude Code also accepts ./CLAUDE.md for backwards compatibility.
_DOT_CLAUDE_RELATIVE = Path(".claude") / "CLAUDE.md"
_LEGACY_RELATIVE = Path("CLAUDE.md")

# Concurrency guard (#366 F1): remove_stale_kernel_block and update_pact_routing
# both perform read-mutate-write on managed CLAUDE.md files. Without a lock,
# two concurrent session_init hooks (e.g., resuming session A while starting
# session B on the same project) can interleave: both processes read the same
# starting content, both compute mutations, both write — last writer wins.
# A sidecar lock (`.{filename}.lock` adjacent to the target) serializes the
# critical sections. Sidecar is chosen over direct target-file locking because:
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

_ROUTING_START_MARKER = "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
_ROUTING_END_MARKER = "<!-- PACT_ROUTING_END -->"

# Outer boundary wrapping all PACT-managed content in project CLAUDE.md.
# User-owned content goes OUTSIDE this block. The name PACT_MANAGED was
# chosen over PACT_START to avoid collision with the old kernel block
# markers that remove_stale_kernel_block() searches for (#404).
MANAGED_START_MARKER = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
MANAGED_END_MARKER = "<!-- PACT_MANAGED_END -->"

# Inner boundary wrapping project memory sections (Retrieved Context,
# Pinned Context, Working Memory) for hook targeting (#404).
MEMORY_START_MARKER = "<!-- PACT_MEMORY_START -->"
MEMORY_END_MARKER = "<!-- PACT_MEMORY_END -->"

# Canonical H1 title for the managed block. Extracted (round 5, item 3) so
# the three template sites (ensure_project_memory_md, _build_migrated_content,
# session_resume.update_session_info Case 0) cannot drift apart. Changing this
# value changes the title everywhere in one place.
MANAGED_TITLE = "# PACT Framework and Managed Project Memory"

# Plugin-managed HTML comment boundary prefixes. Used by parsers and regex
# sites that need to terminate scans on any PACT-managed boundary marker.
# Extracted (round 5, item 1) so the three-prefix union is defined once.
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
# Mirrors the `_BOUNDARY_ALT` constant in `staleness.py` (round 6, item 1):
# any regex that needs to terminate on a PACT boundary marker must embed
# this alternation rather than hard-coding the three-prefix literal. That
# way, adding a fourth prefix to `PACT_BOUNDARY_PREFIXES` automatically
# picks it up everywhere via a one-line constant change.
_BOUNDARY_ALT = "|".join(PACT_BOUNDARY_PREFIXES)

# Stale line from the v3.16.2 project CLAUDE.md template. After the PR #390
# migration the routing block supersedes it, but the stale line lingers in
# upgraded files and contradicts the routing block. Strip it on every
# update_pact_routing() pass. Allows optional trailing period / whitespace.
#
# Round 7 item 2: this pattern is applied per-line by `_strip_legacy_lines`
# via a fence-aware walker, NOT module-wide with `re.MULTILINE`. The per-line
# form is anchored to the full stripped line, so `$` matches end-of-line
# without needing a MULTILINE flag. Removing MULTILINE is load-bearing:
# with MULTILINE the pattern was hot inside user-authored fenced code blocks
# and silently destroyed example content that quoted the stale template line
# (verify-backend-coder-7 counter-test). Per-line application + fence
# tracking prevents that failure mode entirely.
_STALE_ORCHESTRATOR_LINE_RE = re.compile(
    r"^The global PACT Orchestrator is loaded from `~/\.claude/CLAUDE\.md`\.?\s*$",
)


def _strip_legacy_lines(content: str) -> str:
    r"""
    Remove lines from older PACT template versions that are now obsolete.

    Currently strips the stale orchestrator-loader line from the v3.16.2
    project CLAUDE.md template. Shared between `update_pact_routing` (which
    runs on every session_init) and `_build_migrated_content` (which runs
    once per project during the one-shot migration). Centralizing the set
    of legacy-line patterns here means adding a new pattern in the future
    only requires editing this helper, not both call sites.

    Round 7 item 2 — fence-aware: walks `content` line by line, tracks
    markdown fenced-code-block state via `^\s*\`\`\`` (matching
    `staleness._find_terminator_offset` and `_find_preamble_cutoff`
    conventions), and applies `_STALE_ORCHESTRATOR_LINE_RE` ONLY to lines
    that are NOT inside a fence. Lines inside a fence are preserved
    verbatim, even if they match the stale-line regex. This prevents
    silent data loss when a user's CLAUDE.md contains a fenced code block
    that quotes the legacy template verbatim (e.g., migration documentation,
    tutorial content, upgrade change logs).

    Prior (pre-round-7) behavior used `re.MULTILINE` on the whole content,
    which stripped matching lines regardless of fence state. The verify-
    backend-coder-7 counter-test showed: a fenced block containing the
    stale orchestrator line emerged with the line deleted from inside the
    fence, leaving the opening and closing fence markers with an empty
    body. Per-line application plus fence tracking fixes this failure mode.

    Args:
        content: The raw CLAUDE.md content to scrub.

    Returns:
        Content with all legacy template lines OUTSIDE fenced code blocks
        removed. Content inside fenced code blocks is preserved byte for
        byte. Pure function.
    """
    # Walker matches the pattern used by `_find_preamble_cutoff` in this
    # module and `_find_terminator_offset` in staleness.py/working_memory.py.
    # A shared helper was considered but the two consumers have divergent
    # semantics: `_find_preamble_cutoff` stops at first match, this function
    # accumulates every non-matching line. The twin-copy cost is ~15 lines
    # of walker boilerplate — same bar as the existing staleness/working_memory
    # twin and worth the isolation.
    pos = 0
    in_code_fence = False
    fence_re = re.compile(r"^\s*```")
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

        if fence_re.match(line):
            # Fence boundary: toggle state and keep the line verbatim.
            in_code_fence = not in_code_fence
            out_parts.append(raw_segment)
        elif in_code_fence:
            # Inside a fence: keep all content verbatim, regardless of
            # whether the line matches a legacy pattern.
            out_parts.append(raw_segment)
        elif _STALE_ORCHESTRATOR_LINE_RE.match(line):
            # Non-fenced legacy line: drop it entirely (including the
            # trailing newline, matching the pre-round-7 `$\n?` semantics).
            pass
        else:
            out_parts.append(raw_segment)

        pos = line_end

    return "".join(out_parts)


# Canonical PACT routing block template (round 7, item 3). The exact
# HTML-comment-wrapped routing section that every project CLAUDE.md
# must carry: a `<!-- PACT_ROUTING_START -->` opener, the canonical
# `## PACT Routing` instructions, and a `<!-- PACT_ROUTING_END -->`
# closer. Exported as a public symbol (no `_` prefix) because it is
# consumed by `session_resume.update_session_info` and `peer_inject.py`
# when they rewrite the session block template alongside the routing
# block — both writers expect the full wrapper string, not just its
# interior prose.
#
# Drift implications: all three writers — `update_pact_routing`,
# `session_resume.update_session_info`, and the Case 0 session template
# path — rewrite this block on the next session_init pass. The
# idempotency guard in `update_pact_routing` detects drift via a regex
# match: if the on-disk block no longer equals `PACT_ROUTING_BLOCK` byte
# for byte, it is replaced wholesale. Changing the template here changes
# it everywhere on the next SessionStart hook.
PACT_ROUTING_BLOCK = """<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->
## PACT Routing

Before any other work, determine your PACT role and invoke the appropriate
bootstrap skill. Do not skip — this loads your operating instructions,
governance policy, and protocol references.

Check your context for a `PACT ROLE:` marker AT THE START OF A LINE (not
embedded in prose, quoted text, or memory-retrieval results). Hook
injections from `session_init.py` and `peer_inject.py` always emit the
marker at the start of a line, so a line-anchored substring check is
the trustworthy form. Mid-line occurrences of the phrase (e.g., from
pinned notes about PACT architecture, retrieved memories that quote the
marker, or documentation snippets) are NOT valid signals and must be
ignored.

- Line starting with `PACT ROLE: orchestrator` → invoke `Skill("PACT:bootstrap")` unless already loaded.
- Line starting with `PACT ROLE: teammate (` → invoke `Skill("PACT:teammate-bootstrap")` unless already loaded.

No line-anchored marker present? Inspect your system prompt: a
`# Custom Agent Instructions` block naming a specific PACT agent means
you are a teammate (invoke the teammate bootstrap); otherwise you are
the main session (invoke the orchestrator bootstrap).

Re-invoke after compaction if the bootstrap content is no longer present.
<!-- PACT_ROUTING_END -->"""


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


def remove_stale_kernel_block() -> str | None:
    """
    One-time migration: remove the obsolete PACT_START/PACT_END block from
    ~/.claude/CLAUDE.md if present. Preserves all user content outside the
    markers.

    Handles the transition from the PR #390 kernel-in-home-dir architecture
    to the kernel-elimination architecture. Existing installations will have
    a PACT_START/PACT_END delimited block in ~/.claude/CLAUDE.md from previous
    plugin versions; this function strips that block cleanly.

    Called from session_init.py on every SessionStart. Idempotent no-op when
    the markers are absent (i.e., for fresh installs or after first cleanup).

    Hardening (#366):
    - Symlink guard: refuses to operate if ~/.claude/CLAUDE.md is a symlink.
      An attacker with local write access to ~/.claude/ could otherwise
      plant a symlink to redirect plugin writes to an arbitrary target.
      Practical exploitability is low (requires pre-existing local write
      access) but spec Section 6.10 explicitly asks for defensive behavior.
    - Malformed feedback: when the migration skips due to a malformed marker
      state (orphan marker or END-before-START), returns the warning as a
      status string so session_init.py surfaces it via systemMessage to
      the user. Hook stderr is NOT shown to users by Claude Code, so a
      returned string is the only way to deliver the warning.

    Returns:
        Status message on successful removal, None on no-op (clean, absent
        markers) or error, or a "Migration skipped: ..." string on defensive
        no-op (malformed marker state; session_init.py routes these to
        systemMessages via the "failed"/"skipped" check).
    """
    target_file = Path.home() / ".claude" / "CLAUDE.md"
    if not target_file.exists():
        return None

    # Concurrency guard (#366 F1): serialize read-mutate-write so two
    # concurrent session_init hooks on the same home file cannot clobber
    # each other. Fail-open on timeout — next session start will retry.
    try:
        with file_lock(target_file):
            # Symlink guard INSIDE the lock (#366 R5 M1, TOCTOU defense):
            # is_symlink uses lstat under the hood which does NOT follow the
            # link, so this is safe even if the link target is itself a
            # malicious file. Inside the lock so an attacker cannot swap the
            # target between an outside-lock check and the write. The status
            # string is deliberately opaque — it identifies WHAT was skipped
            # without revealing the internal check that triggered the skip to
            # a local attacker inspecting the output.
            if target_file.is_symlink():
                return (
                    "Migration skipped: ~/.claude/CLAUDE.md path precondition not met."
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
                return None  # Normal idempotent no-op for already-migrated installs

            if has_start != has_end:
                # Only one of the two markers is present. Defensive no-op to avoid
                # data loss, and return a status string so session_init.py surfaces
                # the warning via systemMessage. This case can occur if a prior
                # plugin write crashed mid-file or the user manually deleted one
                # marker.
                which = "PACT_START" if has_start else "PACT_END"
                missing = "PACT_END" if has_start else "PACT_START"
                return (
                    f"Migration skipped: ~/.claude/CLAUDE.md contains {which} but "
                    f"no matching {missing}. To avoid data loss, inspect the file "
                    f"and either remove the orphan {which} marker or restore the "
                    f"matching {missing} marker."
                )

            pre_marker, rest = content.split(START_MARKER, 1)
            if END_MARKER not in rest:
                # END marker exists in content but appears textually before START.
                # Same defensive handling.
                return (
                    "Migration skipped: ~/.claude/CLAUDE.md contains both PACT_START "
                    "and PACT_END markers but PACT_END appears before PACT_START. "
                    "Inspect the file and reorder or remove the orphan markers."
                )

            _, post_marker = rest.split(END_MARKER, 1)

            # Preserve one blank line at the removal boundary so the user's
            # spacing around the obsolete block survives the strip.
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
                return "Removed obsolete PACT kernel block from ~/.claude/CLAUDE.md"
            except OSError as e:
                return f"Failed to remove stale kernel block: {str(e)[:50]}"
    except TimeoutError:
        return (
            "Failed to acquire lock on ~/.claude/CLAUDE.md within 5s "
            "(another session_init hook may be running concurrently). "
            "Kernel-block migration skipped; will retry on next session start."
        )


def update_pact_routing() -> str | None:
    """
    Ensure the project CLAUDE.md contains the canonical PACT_ROUTING block.

    Idempotent: on every SessionStart, find the PACT_ROUTING_START/PACT_ROUTING_END
    markers in the project CLAUDE.md and replace content between them with the
    canonical routing block. If markers are absent, insert the block near the
    top of the file below the title. If the file doesn't exist, defer to
    ensure_project_memory_md() which creates it with the block in its template.

    Preserves all user content outside the markers.

    Hardening (#366):
    - Symlink guard: refuses to operate if the project CLAUDE.md is a
      symlink. Same rationale as remove_stale_kernel_block — prevents
      redirected writes via planted symlinks.
    - Orphan marker handling: if exactly one of PACT_ROUTING_START or
      PACT_ROUTING_END is present (e.g., user manually deleted the
      closing marker, or a prior write crashed mid-file), strip the
      orphan marker before falling through to the insert path. This
      prevents the bug where the file accumulates a new routing block
      on every session because the update-path guard requires BOTH
      markers to be present.

    Returns:
        Status message on change, "Routing skipped: ..." on defensive
        no-op (symlink), or None when no write was needed.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    target_file, source = resolve_project_claude_md_path(project_dir)

    if source == "new_default":
        # File doesn't exist yet; ensure_project_memory_md() will create it
        # with the routing block in its template.
        return None

    # Concurrency guard (#366 F1): serialize read-mutate-write so two
    # concurrent session_init hooks on the same project CLAUDE.md cannot
    # interleave with each other (or with update_session_info's write) and
    # clobber the SESSION_START block. Fail-open on timeout — next session
    # start will retry.
    try:
        with file_lock(target_file):
            # Symlink guard INSIDE the lock (#366 R5 M1, TOCTOU defense):
            # same defensive guard as remove_stale_kernel_block. is_symlink
            # uses lstat so it does not follow the link. Inside the lock so
            # an attacker cannot swap the target between an outside-lock
            # check and the write. Return the warning as a status string so
            # session_init.py surfaces it via systemMessage (hook stderr is
            # not shown to users). The status string is deliberately opaque
            # — see remove_stale_kernel_block.
            if target_file.is_symlink():
                return (
                    f"Routing skipped: {target_file} path precondition not met."
                )

            try:
                content = target_file.read_text(encoding="utf-8")
            except OSError:
                return None

            stripped = _strip_legacy_lines(content)
            stale_line_removed = stripped != content
            content = stripped

            # Case 1: markers present — replace content between them
            if _ROUTING_START_MARKER in content and _ROUTING_END_MARKER in content:
                pattern = re.compile(
                    re.escape(_ROUTING_START_MARKER) + r".*?" + re.escape(_ROUTING_END_MARKER),
                    re.DOTALL,
                )
                new_content = pattern.sub(PACT_ROUTING_BLOCK, content)

                if new_content == content and not stale_line_removed:
                    return None  # Already canonical and no stale line to strip

                try:
                    target_file.write_text(new_content, encoding="utf-8")
                    os.chmod(str(target_file), 0o600)
                    if new_content == content:
                        return (
                            "Removed stale orchestrator-loader line from project "
                            "CLAUDE.md (routing block already canonical)"
                        )
                    if stale_line_removed:
                        return (
                            "PACT routing block updated in project CLAUDE.md "
                            "(stripped stale orchestrator-loader line)"
                        )
                    return "PACT routing block updated in project CLAUDE.md"
                except OSError as e:
                    return f"Failed to update PACT routing: {str(e)[:50]}"

            # Orphan marker handling: if exactly one of the two markers is present
            # (the other was manually deleted, or a prior write crashed mid-file),
            # strip the orphan marker before falling through to the insert path.
            # Without this, the insert path blindly prepends a new routing block
            # on every session because the update guard requires BOTH markers to
            # be present — leading to file accumulation over N sessions.
            #
            # SESSION_START isolation (#366 item 5): the strip is scoped to lines
            # OUTSIDE any <!-- SESSION_START --> ... <!-- SESSION_END --> region.
            # Without this scope, a SESSION_START block whose content happened to
            # contain a line matching the routing marker (pathological but
            # possible — e.g., user pasted routing-block docs into session
            # metadata) would be silently corrupted: the orphan strip would drop
            # the line, then the insert path would add a fresh routing block at
            # the top while the SESSION_START body was left missing a line.
            has_start = _ROUTING_START_MARKER in content
            has_end = _ROUTING_END_MARKER in content
            orphan_stripped = False
            if has_start != has_end:
                # Strip whichever orphan marker is present. The text on the same
                # line as the marker is also stripped if the marker is on its
                # own line — otherwise just remove the marker substring. Lines
                # inside a SESSION_START/SESSION_END region are preserved
                # verbatim even if they contain the marker substring.
                orphan = _ROUTING_START_MARKER if has_start else _ROUTING_END_MARKER
                content_lines = content.splitlines(keepends=True)
                cleaned_lines = []
                inside_session_block = False
                for ln in content_lines:
                    if "<!-- SESSION_START -->" in ln:
                        inside_session_block = True
                        cleaned_lines.append(ln)
                        continue
                    if "<!-- SESSION_END -->" in ln:
                        inside_session_block = False
                        cleaned_lines.append(ln)
                        continue
                    if inside_session_block:
                        # Preserve SESSION_START body verbatim — the strip
                        # must never reach into this region.
                        cleaned_lines.append(ln)
                        continue
                    if orphan in ln and ln.strip() == orphan:
                        # Whole-line marker — drop the line entirely
                        continue
                    elif orphan in ln:
                        # Inline marker — strip just the substring
                        cleaned_lines.append(ln.replace(orphan, ""))
                    else:
                        cleaned_lines.append(ln)
                content = "".join(cleaned_lines)
                orphan_stripped = True

            # Case 2: markers absent — insert near the top of the file below the title
            lines = content.splitlines(keepends=True)
            insert_idx = 0
            for i, line in enumerate(lines):
                if line.startswith("# "):
                    insert_idx = i + 1
                    # Skip any blank lines or short description lines immediately
                    # after the title before inserting.
                    for j in range(i + 1, min(i + 6, len(lines))):
                        if lines[j].startswith("##") or lines[j].startswith("<!--"):
                            insert_idx = j
                            break
                        insert_idx = j + 1
                    break

            new_lines = (
                lines[:insert_idx]
                + ["\n", PACT_ROUTING_BLOCK + "\n", "\n"]
                + lines[insert_idx:]
            )
            new_content = "".join(new_lines)

            try:
                target_file.write_text(new_content, encoding="utf-8")
                os.chmod(str(target_file), 0o600)
                notes = []
                if orphan_stripped:
                    notes.append("stripped orphan marker from prior incomplete write")
                if stale_line_removed:
                    notes.append("stripped stale orchestrator-loader line")
                if notes:
                    return (
                        "PACT routing block inserted into project CLAUDE.md ("
                        + "; ".join(notes)
                        + ")"
                    )
                return "PACT routing block inserted into project CLAUDE.md"
            except OSError as e:
                return f"Failed to insert PACT routing: {str(e)[:50]}"
    except TimeoutError:
        return (
            "Failed to acquire lock on project CLAUDE.md within 5s "
            "(another session_init hook may be running concurrently). "
            "Routing update skipped; will retry on next session start."
        )


def ensure_project_memory_md() -> str | None:
    """
    Ensure project has a CLAUDE.md with memory sections.

    Creates a minimal project-level CLAUDE.md containing the PACT-managed
    structure: outer PACT_MANAGED boundary, routing block, session block,
    and inner PACT_MEMORY boundary wrapping memory sections (Retrieved
    Context, Pinned Context, Working Memory) if one doesn't exist.
    These sections are project-specific and managed by the pact-memory skill.

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

{PACT_ROUTING_BLOCK}

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
    # and race on the write. Same pattern as remove_stale_kernel_block and
    # update_pact_routing. Fail-open on timeout — next session start retries.
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
    pattern as remove_stale_kernel_block(): file_lock, symlink guard inside
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
    (round 5, item 9).

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


# Trigger list for preamble detection (round 6, item 4). If any of these
# literals appear in the pre-migration content, the region ABOVE the earliest
# occurrence is user-owned "preamble" that must be preserved ABOVE the
# PACT_MANAGED block after migration. The list covers:
#   - Legacy H1 / H2 section headings from older templates
#   - PACT-managed HTML comment markers (routing, session, memory)
# Trigger matching is fence-aware (round 7, item 1): occurrences inside a
# markdown fenced code block (```...```) are ignored so a user's example
# PACT content is not treated as real managed content. The cutoff lands at
# the earliest trigger appearing in NON-fenced prose, or at `len(content)`
# when no trigger exists outside fences. Prior behavior used naive
# substring search, which composed unsafely with the downstream
# `_strip_legacy_lines` + `^# Project Memory` `re.sub` at line 971: a
# fenced example containing `# Project Memory` + routing markers saw its
# heading stripped and routing block extracted from inside the fence,
# destroying the user's example block.
_PREAMBLE_TRIGGERS: tuple[str, ...] = (
    "# Project Memory",
    "## PACT Routing",
    "## Current Session",
    "## Retrieved Context",
    "## Pinned Context",
    "## Working Memory",
    _ROUTING_START_MARKER,
    "<!-- SESSION_START -->",
    MEMORY_START_MARKER,
)


def _find_preamble_cutoff(content: str) -> int:
    """
    Return the byte offset where user-owned preamble ends and PACT-managed
    content begins in the original (pre-migration) CLAUDE.md content.

    Walks the content line by line, tracking markdown fenced code block
    state (toggled on any line whose stripped form starts with ```). For
    each non-fenced line, checks whether any trigger in
    `_PREAMBLE_TRIGGERS` occurs in that line and returns the byte offset
    of the earliest such occurrence. Triggers that appear inside a fenced
    code block are skipped — they are treated as user example content, not
    real PACT-managed content.

    Returns `len(content)` if no trigger is found in any non-fenced line
    (in which case the entire file is preamble and the migration will
    emit a shell managed block with empty memory).

    Round 6 item 4: pre-fix behavior put all non-memory content AFTER
    PACT_MANAGED_END. Users with notes above `# Project Memory` saw their
    content moved below all PACT-managed content. The fix split the file
    at the earliest PACT-managed trigger so preamble user content
    survives in place above PACT_MANAGED_START.

    Round 7 item 1: the round-6 implementation used naive substring
    search (`content.find(trigger)`). That composed unsafely with the
    downstream `_strip_legacy_lines` + `^# Project Memory` `re.sub` at
    line 971: a user example inside a fenced code block containing
    `# Project Memory` + routing markers saw its heading stripped, its
    routing block extracted from inside the fence, and its closing fence
    left orphaned in the trailing user region. The fence-aware walker
    makes triggers inside fences invisible to the cutoff scan, so the
    entire example block survives as preamble.

    Args:
        content: The pre-migration CLAUDE.md content.

    Returns:
        Byte offset of the first PACT-managed trigger outside any fenced
        code block, or `len(content)` if no trigger is found outside
        fences.
    """
    pos = 0
    in_code_fence = False
    fence_re = re.compile(r"^\s*```")
    while pos < len(content):
        nl = content.find("\n", pos)
        if nl == -1:
            line = content[pos:]
            line_end = len(content)
        else:
            line = content[pos:nl]
            line_end = nl + 1

        if fence_re.match(line):
            in_code_fence = not in_code_fence
        elif not in_code_fence:
            # Scan this line for the earliest trigger. Return the absolute
            # offset (line start + trigger offset within the line).
            earliest_in_line = -1
            for trigger in _PREAMBLE_TRIGGERS:
                idx = line.find(trigger)
                if idx != -1 and (earliest_in_line == -1 or idx < earliest_in_line):
                    earliest_in_line = idx
            if earliest_in_line != -1:
                return pos + earliest_in_line

        pos = line_end

    return len(content)


def _build_migrated_content(content: str) -> str:
    """
    Transform old-format CLAUDE.md content into the new managed structure.

    Extracts the PACT-managed sections (routing, session, memory) from the
    existing content and reassembles them inside the new boundary markers.
    Any content that falls outside the recognized PACT sections is preserved
    AFTER the PACT_MANAGED_END marker as user-owned content.

    Round 6 item 4: User content that appears BEFORE the first PACT-managed
    marker/heading in the original file is preserved ABOVE the PACT_MANAGED_START
    marker as "preamble" (instead of being shoved below PACT_MANAGED_END with
    the rest of the non-memory user content). This preserves the intuition
    that edits a user makes at the top of the file stay at the top.

    This is a pure function (no I/O) for testability.

    Idempotency guard (round 5, item 2): if the content already contains
    MANAGED_START_MARKER, return it unchanged. The integration wrapper
    migrate_to_managed_structure also has this guard, but duplicating it
    here means any caller (including tests and future consumers) gets the
    safety for free and double-passes can never double-wrap.

    Args:
        content: The existing CLAUDE.md file content.

    Returns:
        The restructured content with PACT_MANAGED and PACT_MEMORY boundaries,
        or the original content unchanged if already migrated.
    """
    # Idempotency guard: already migrated → no-op
    if MANAGED_START_MARKER in content:
        return content

    # Preamble split (round 6, item 4): compute cutoff on ORIGINAL content
    # before any extraction. Walker-level preamble detection won't work here
    # because by the time the walker runs, routing/session have been extracted
    # and the old `# Project Memory` heading has been stripped by
    # _strip_legacy_lines — the walker cannot see the triggers that define
    # "where does preamble end". Computing the cutoff first means all trigger
    # types (including HTML comment markers) are detectable.
    preamble_cutoff = _find_preamble_cutoff(content)
    preamble_text = content[:preamble_cutoff]
    # Apply _strip_legacy_lines to the preamble too. An upgraded project may
    # have the v3.16.2 stale orchestrator-loader line in the preamble region
    # (above `# Project Memory`), and leaving it there would contradict the
    # routing block that gets reinstalled below. Round 7 item 2:
    # `_strip_legacy_lines` is fence-aware, so a user-authored fenced code
    # block in the preamble region that quotes the stale line verbatim (e.g.,
    # migration documentation, tutorial content) is preserved intact. Only
    # non-fenced matches of the stale line are stripped.
    preamble_text = _strip_legacy_lines(preamble_text)
    content = content[preamble_cutoff:]

    # Extract routing block if present (between markers)
    routing_block = ""
    content_sans_routing = content
    if _ROUTING_START_MARKER in content and _ROUTING_END_MARKER in content:
        pattern = re.compile(
            re.escape(_ROUTING_START_MARKER) + r".*?" + re.escape(_ROUTING_END_MARKER),
            re.DOTALL,
        )
        match = pattern.search(content)
        if match:
            routing_block = match.group(0)
            content_sans_routing = content[:match.start()] + content[match.end():]
    elif _ROUTING_START_MARKER in content or _ROUTING_END_MARKER in content:
        # Orphan PACT_ROUTING marker (round 5, item 5): exactly one of
        # START/END is present. This happens if a user manually edits the
        # routing block and deletes half of it, or if a partial write
        # corrupts the file. Without this branch, the orphan marker would
        # be preserved in memory_parts or user_parts and the downstream
        # update_pact_routing would see a half-block it refuses to touch
        # (because its regex requires both markers), leaving the project
        # in a permanently broken routing state.
        #
        # Recovery strategy: drop the orphan marker AND any adjacent
        # `## PACT Routing` H2 block (before or after the orphan marker).
        # A fresh routing block will be installed by the next
        # update_pact_routing call. We lose no information — the canonical
        # routing content is a plugin template rebuilt from
        # PACT_ROUTING_BLOCK, not user-authored content.
        orphan_marker = (
            _ROUTING_START_MARKER
            if _ROUTING_START_MARKER in content
            else _ROUTING_END_MARKER
        )
        marker_idx = content.find(orphan_marker)

        # Find the start of the strip region: prefer the preceding
        # `## PACT Routing` heading if it appears just before the orphan
        # marker (within the last ~200 chars). This handles the case
        # where the routing-end marker is orphaned but the heading and
        # prose are still upstream.
        preamble_start = max(0, marker_idx - 200)
        preamble = content[preamble_start:marker_idx]
        heading_match = re.search(r"\n## PACT Routing\s*\n", preamble)
        if heading_match:
            strip_start = preamble_start + heading_match.start()
        else:
            strip_start = marker_idx

        # Find the end of the strip region. Scan forward from the line
        # AFTER the orphan marker, and also consume an immediately
        # following `## PACT Routing` heading + body (handles the
        # routing-start orphan case where the heading sits between the
        # marker and the next terminator).
        scan_from = content.find("\n", marker_idx)
        if scan_from == -1:
            scan_from = len(content)
        else:
            scan_from += 1

        # Skip over an adjacent `## PACT Routing` heading if present
        # (the heading itself must be stripped along with the orphan).
        post_heading_match = re.match(
            r"## PACT Routing\s*\n",
            content[scan_from:],
        )
        if post_heading_match:
            scan_from += post_heading_match.end()

        # Find the next genuine section terminator (non-PACT_Routing
        # heading or any PACT boundary marker).
        # Uses _BOUNDARY_ALT so adding a prefix to PACT_BOUNDARY_PREFIXES
        # is still a one-line change — this was the 6th drift site that
        # hard-coded the three-prefix literal (round 6, item 1).
        next_terminator = re.search(
            rf"^(?:#{{1,2}}\s|<!-- (?:{_BOUNDARY_ALT}))",
            content[scan_from:],
            re.MULTILINE,
        )
        if next_terminator:
            strip_end = scan_from + next_terminator.start()
        else:
            strip_end = len(content)

        content_sans_routing = content[:strip_start] + content[strip_end:]

    # Extract session block if present (between markers)
    session_block = ""
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
    # memory sections and user content. Extract memory sections by heading.
    remaining = content_sans_session

    # Remove the old top-level heading and description line via a local
    # anchored `re.sub`. This strips `^# Project Memory\s*\n` (plus an
    # optional description line) at the start of `remaining` — note that
    # `remaining` is post-cutoff content, so the match can only fire when
    # the first line of the managed region was the legacy `# Project
    # Memory` heading.
    remaining = re.sub(
        r"^# Project Memory\s*\n"
        r"(?:\s*\n)*"
        r"(?:This file contains project-specific memory managed by the PACT framework\.\s*\n)?",
        "",
        remaining,
    )

    # Apply `_strip_legacy_lines` to scrub any OTHER v3.16.2 template
    # remnants that lingered in the managed region — currently just the
    # stale orchestrator-loader line. This is a DIFFERENT stripper from
    # the `re.sub` immediately above:
    #   - The `re.sub` immediately above targets the `# Project Memory` H1
    #     at the start of `remaining`. It uses `^` without `re.MULTILINE`
    #     so it can only match at position 0 of `remaining`.
    #   - `_strip_legacy_lines` targets the stale orchestrator-loader line
    #     ("The global PACT Orchestrator is loaded from ..."). It is
    #     fence-aware (round 7, item 2): internally walks lines and only
    #     applies the per-line pattern to non-fenced lines.
    #
    # Both run on post-cutoff content (`remaining`). Both are safe from
    # the fenced-code-block failure mode, by DIFFERENT mechanisms:
    #   - The `re.sub` above is safe because it has no `re.MULTILINE`
    #     flag: `^` matches only the absolute start of `remaining`, and
    #     `_find_preamble_cutoff` (round 7, item 1, fence-aware) guarantees
    #     `remaining` never starts inside a user-authored fence. A fenced
    #     `# Project Memory` is pushed up into the preamble region instead.
    #   - `_strip_legacy_lines` is safe because its own walker skips any
    #     line inside a fence. Even if a fenced stale-orchestrator line
    #     somehow reached `remaining` (e.g. an adversarial case where the
    #     fence opens AFTER the preamble cutoff), the walker would preserve
    #     it verbatim. The upstream fence-aware cutoff plus the downstream
    #     fence-aware stripper are belt-and-suspenders.
    #
    # Caveat: if a user has literal legacy patterns verbatim at the TOP
    # of `remaining` (i.e., at the earliest non-fenced position of their
    # file — effectively at the start of the managed region), the
    # stripping is correct by design. Those ARE the patterns being
    # migrated away from, and removing them is the whole point of the
    # migration pass.
    remaining = _strip_legacy_lines(remaining)

    # Extract memory sections: Retrieved Context, Pinned Context, Working Memory
    # These are identified by their ## headings. Everything from the first
    # memory heading to the end of the last memory section (or EOF) is memory.
    memory_headings = ["## Retrieved Context", "## Pinned Context", "## Working Memory"]
    memory_parts = []
    user_parts = []

    lines = remaining.splitlines(keepends=True)
    current_section: list[str] = []
    in_memory_section = False
    # Track markdown code fence state so a ``` ... ## Pinned Context ... ```
    # block inside user content is not misclassified as a real memory section.
    # Fence detection: stripped line starts with ``` (with optional language tag).
    in_code_fence = False

    for line in lines:
        stripped = line.rstrip()
        # Toggle fence state BEFORE heading detection so the fence marker line
        # itself is accumulated into whichever bucket we're currently in.
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            current_section.append(line)
            continue
        # While inside a code fence, treat all content as opaque — no heading
        # detection, so fenced `## Pinned Context` stays with the surrounding
        # user content instead of being extracted as memory.
        if in_code_fence:
            current_section.append(line)
            continue
        # Check if this line starts a memory section (exact match after rstrip)
        if any(stripped == h for h in memory_headings):
            # Flush any non-memory content accumulated before this heading
            if current_section and not in_memory_section:
                user_parts.extend(current_section)
                current_section = []
            elif current_section and in_memory_section:
                memory_parts.extend(current_section)
                current_section = []
            in_memory_section = True
            current_section.append(line)
        elif stripped.startswith("## ") or stripped.startswith("# "):
            # A non-memory heading — flush current section
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

    # Flush remaining
    if current_section:
        if in_memory_section:
            memory_parts.extend(current_section)
        else:
            user_parts.extend(current_section)

    memory_text = "".join(memory_parts).strip()
    user_text = "".join(user_parts).strip()

    # Split preserved memory_text into a dict of {heading: body} so we can
    # always emit all three canonical headings in order, using empty bodies
    # for any that weren't in the source. Downstream consumers (working_memory
    # parser, staleness checker, pact-memory skill) rely on the headings being
    # present as insert points; a missing heading would break those writers.
    #
    # Duplicate headings: if the source has the same memory heading twice
    # (e.g., two `## Working Memory` blocks), their bodies are concatenated
    # into a single output block so no content is lost. This matches the
    # pre-Item-5 behavior where memory_text was emitted verbatim.
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

    # Build the new structure. Preamble (round 6, item 4): if the user had
    # content ABOVE the first PACT-managed trigger, emit it FIRST, then a
    # blank-line separator, then the managed block. Empty preamble (common
    # case: file starts with `# Project Memory`) contributes nothing.
    parts: list[str] = []
    preamble_stripped = preamble_text.strip()
    if preamble_stripped:
        parts.extend([preamble_stripped, "\n\n"])
    parts.extend([MANAGED_START_MARKER, "\n", f"{MANAGED_TITLE}\n"])

    # Routing block
    if routing_block:
        parts.extend(["\n", routing_block, "\n"])

    # Session block
    if session_block:
        parts.extend(["\n", session_block, "\n"])

    # Memory block — no interior H1; memory sections begin directly with H2
    # headings, matching the shape in ensure_project_memory_md's template.
    # All three canonical headings are always emitted in order, even if the
    # source only had a subset. Missing sections get empty bodies.
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

    # Close managed block
    parts.extend(["\n", MANAGED_END_MARKER, "\n"])

    # Append user content outside the managed block
    if user_text:
        parts.extend(["\n", user_text, "\n"])

    return "".join(parts)
